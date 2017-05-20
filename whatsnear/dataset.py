# coding=utf-8
import time
import json
import math
from database import Database


class Dataset(object):
    def __init__(self):
        # training related variables
        self._labels = []
        self._features = []

        # global parameters
        self._mean_category_number = {}
        self._category_coefficient = {}
        self._categories = {}
        self._database = None
        self.is_ready = False

    def vectorize_point(self, neighbors, training_category):
        neighbor_categories = self._neighbor_categories(neighbors)

        x = []

        # density
        x.append(len(neighbors))

        # neighbors entropy
        entropy = 0
        for (key, value) in neighbor_categories.items():
            if value == 0:
                continue
            entropy += (float(value) / len(neighbors)) * -1 * math.log(float(value) / len(neighbors))

        x.append(entropy)

        # competitiveness
        competitiveness = 0
        if training_category in neighbor_categories:
            competitiveness = -1 * float(neighbor_categories[training_category]) / len(neighbors)

        x.append(competitiveness)

        # quality by jensen
        jenson_quality = 0
        for category, _ in self._categories.items():
            if self._category_coefficient[category][training_category] == 0:
                continue

            jenson_quality += math.log(self._category_coefficient[category][training_category]) * (
                neighbor_categories[category] - self._mean_category_number[category][training_category])

        x.append(jenson_quality)

        # area popularity
        popularity = 0
        for neighbor in neighbors:
            popularity += int(neighbor['checkins'])

        x.append(popularity)
        return x

    def load(self, path):
        print('[Dataset] Pre-calculated train file found, loading from external file...')
        start_time = time.clock()

        with open(path, 'r') as f:
            self._mean_category_number = json.loads(f.readline())
            self._category_coefficient = json.loads(f.readline())
            self._categories = json.loads(f.readline())
            self._labels = json.loads(f.readline())
            self._features = json.loads(f.readline())

        end_time = time.clock()
        print('[Dataset] Training data read in %f seconds.' % (end_time - start_time))

    def save(self, path):
        with open(path, 'w') as f:
            f.write(json.dumps(self._mean_category_number) + '\n')
            f.write(json.dumps(self._category_coefficient) + '\n')
            f.write(json.dumps(self._categories) + '\n')
            f.write(json.dumps(self._labels) + '\n')
            f.write(json.dumps(self._features))
        print('[Dataset] Calculated training data stored into %s.' % path)

    def get_features(self):
        return self._features

    def get_labels(self):
        return self._labels

    def _neighbor_categories(self, neighbors):
        # calculate sub-global parameters
        neighbor_categories = {}
        for category, _ in self._categories.items():
            neighbor_categories[category] = 0
        for neighbor in neighbors:
            neighbor_categories[neighbor['category']] += 1
        return neighbor_categories

    def _split_range(self, max_num, step):
        parts = []
        for i in xrange(0, max_num, step):
            if i + step < max_num:
                parts.append([i, step])
            else:
                parts.append([i, max_num - i])
        return parts

    def _progress_process(self, title, max, progress_queue):
        from progress.bar import Bar
        bar = Bar(title, suffix='%(index)d / %(max)d, %(percent)d%%', max=max)
        cur = 0
        while cur != max:
            progress = progress_queue.get()
            for _ in range(progress):
                bar.next()
            cur += progress
        bar.finish()

    def _calculate_mean_category(self):
        import multiprocessing as mp

        # initialize the queues
        result_queue = mp.Queue()
        progress_queue = mp.Queue()

        # radius to calculate features
        r = 200

        # calculate global parameters
        total_num = self._database.get_total_num()

        def calculate_local_mean_category(database, part, categories, result_queue, progress_queue):
            # initialize local matrix
            mean_category_number = {}
            for outer, _ in categories.items():
                mean_category_number[outer] = {}
                for inner, _ in categories.items():
                    mean_category_number[outer][inner] = 0

            database = Database(database)
            # calculate mean category numbers
            for row in database.get_connection().execute(
                            '''SELECT lng,lat,geohash,category,checkins,id FROM \'Beijing-Checkins\' LIMIT %d,%d''' % (
                            part[0], part[1])):
                neighbors = database.get_neighboring_points(float(row[0]), float(row[1]), r, geo=unicode(row[2]))
                for neighbor in neighbors:
                    mean_category_number[neighbor['category']][unicode(row[3])] += 1
                progress_queue.put(1)

            result_queue.put(mean_category_number)

        progress_process = mp.Process(target=self._progress_process,
                                      args=('Calculating mean category numbers', total_num, progress_queue))
        progress_process.start()

        parts = self._split_range(total_num, total_num / mp.cpu_count())
        processes = []
        for i in xrange(mp.cpu_count()):
            process = mp.Process(target=calculate_local_mean_category, args=(
                self._database.get_file_path(), parts[i], self._categories, result_queue, progress_queue))
            processes.append(process)
            process.start()

        print('[Dataset] Starting %d processes.' % mp.cpu_count())

        for process in processes:
            process.join()

        progress_process.join()

        print('[Dataset] Processes terminated.')

        # retrieve and merge the results
        for i in range(len(processes)):
            mean_category_number = result_queue.get()
            for p, _ in self._categories.items():
                for l, _ in self._categories.items():
                    self._mean_category_number[p][l] += mean_category_number[p][l]
                    # TODO: to delete this line of code when we run training in full dataset
                    if self._categories[l] == 0:
                        continue
                    mean_category_number[p][l] /= self._categories[l]

    def _calculate_category_coefficients(self):
        import multiprocessing as mp

        # initialize the queues
        result_queue = mp.Queue()
        progress_queue = mp.Queue()

        # radius to calculate features
        r = 200

        # calculate global parameters
        total_num = self._database.get_total_num()

        def calculate_local_category_coefficients(database, part, neighbor_category, categories, result_queue, progress_queue):
            # initialize local matrix
            category_coefficients = {}
            for outer, _ in categories.items():
                category_coefficients[outer] = {}
                for inner, _ in categories.items():
                    category_coefficients[outer][inner] = 0

            for (p, l) in part:
                # TODO: delete this line of code in full dataset
                if self._categories[p] * self._categories[l] == 0:
                    continue

                database = Database(database)

                k_prefix = float(total_num - self._categories[p]) / (self._categories[p] * self._categories[l])

                k_suffix = 0
                for row in database.get_connection().execute(
                        '''SELECT lng,lat,geohash,category,checkins,id FROM \'Beijing-Checkins\''''):
                    if unicode(row[3]) == p:
                        neighbors = database.get_neighboring_points(float(row[0]), float(row[1]), r,
                                                                          geo=unicode(row[2]))
                        neighbor_categories = neighbor_category(neighbors)

                        if len(neighbors) - neighbor_categories[p] == 0:
                            continue

                        k_suffix += float(neighbor_categories[l]) / (len(neighbors) - neighbor_categories[p])

                category_coefficients[p][l] = k_prefix * k_suffix
                progress_queue.put(1)

            result_queue.put(category_coefficients)

        progress_process = mp.Process(target=self._progress_process,
                                      args=('Calculating category coefficients', len(self._categories) * len(self._categories), progress_queue))
        progress_process.start()

        parts = []
        for p, _ in self._categories.items():
            for l, _ in self._categories.items():
                parts.append((p, l))

        print('[Dataset] Starting %d processes.' % mp.cpu_count())
        step = int(len(parts) / mp.cpu_count())
        parts = [parts[i:i + step] for i in xrange(0, len(parts), step)]
        processes = []
        for i in xrange(mp.cpu_count()):
            process = mp.Process(target=calculate_local_category_coefficients, args=(
                self._database.get_file_path(), parts[i], self._neighbor_categories, self._categories, result_queue, progress_queue))
            processes.append(process)
            process.start()

        for process in processes:
            process.join()

        progress_process.join()

        print('[Dataset] Processes terminated.')

        # retrieve and merge the results
        for part in parts:
            category_coefficients = result_queue.get()
            for (p, l) in part:
                self._category_coefficient[p][l] = category_coefficients[p][l]

    def _calculate_features(self):
        import multiprocessing as mp

        # initialize the queues
        result_queue = mp.Queue()
        progress_queue = mp.Queue()

        # radius to calculate features
        r = 200

        # calculate global parameters
        total_num = self._database.get_total_num()

        def calculate_features(database, part, vectorize_point, result_queue, progress_queue):
            # initialize local matrix
            labels = []
            features = []

            database = Database(database)
            # calculate mean category numbers
            for row in database.get_connection().execute(
                            '''SELECT lng,lat,geohash,category,checkins,id FROM \'Beijing-Checkins\' LIMIT %d,%d''' % (
                            part[0], part[1])):
                neighbors = self._database.get_neighboring_points(float(row[0]), float(row[1]), r, geo=unicode(row[2]))
                # add label
                labels.append([int(row[4])])
                # add feature
                features.append(vectorize_point(neighbors, u'生活娱乐'))
                progress_queue.put(1)

            result_queue.put((labels, features))

        progress_process = mp.Process(target=self._progress_process,
                                      args=('Calculating features', total_num, progress_queue))
        progress_process.start()

        print('[Dataset] Starting %d processes.' % mp.cpu_count())
        parts = self._split_range(total_num, total_num / mp.cpu_count())
        processes = []
        for i in xrange(mp.cpu_count()):
            process = mp.Process(target=calculate_features, args=(
                self._database.get_file_path(), parts[i], self.vectorize_point, result_queue, progress_queue))
            processes.append(process)
            process.start()

        for process in processes:
            process.join()

        progress_process.join()

        print('[Dataset] Processes terminated.')

        # retrieve results
        for i in range(len(processes)):
            labels, features = result_queue.get()
            self._labels.extend(labels)
            self._features.extend(features)

    def prepare(self, database):
        print('[Dataset] Pre-calculated train file not found, calculating training data...')
        start_time = time.clock()
        self._database = Database(database)

        self._database.update_geohash()
        self._categories = self._database.get_categories()

        # calculate and store the neighboring points

        # bar = Bar('Calculating neighbors', suffix='%(index)d / %(max)d, %(percent)d%%', max=total_num)
        # for row in self._database.get_connection().execute('''SELECT lng,lat,geohash,category,checkins,id FROM \'Beijing-Checkins\''''):
        #    self._all_points.append({
        #        'id': int(row[5]),
        #        'checkins': int(row[4]),
        #        'category': unicode(row[3]),
        #        'neighbors': self._database.get_neighboring_points(float(row[0]), float(row[1]), r, geo=unicode(row[2]))
        #    })
        #    bar.next()
        # bar.finish()

        # initialize the matrix
        for outer, _ in self._categories.items():
            self._mean_category_number[outer] = {}
            self._category_coefficient[outer] = {}
            for inner, _ in self._categories.items():
                self._mean_category_number[outer][inner] = 0
                self._category_coefficient[outer][inner] = 0

        # calculate global category parameters
        self._calculate_mean_category()
        self._calculate_category_coefficients()
        self._calculate_features()

        end_time = time.clock()
        print('[RankNet] Training data calculated in %f seconds.' % (end_time - start_time))

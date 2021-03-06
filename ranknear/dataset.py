import time
import json
import math
import logging
from ranknear.database import Database


logger = logging.getLogger(__name__)


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
        if training_category in neighbor_categories and len(neighbors) != 0:
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
        logger.info('Pre-calculated train file found, loading from external file...')
        start_time = time.time()

        with open(path, 'r') as f:
            self._mean_category_number = json.loads(f.readline())
            self._category_coefficient = json.loads(f.readline())
            self._categories = json.loads(f.readline())
            self._labels = json.loads(f.readline())
            self._features = json.loads(f.readline())

        end_time = time.time()
        logger.info('Training data read in %f seconds.' % (end_time - start_time))

    def save(self, path):
        with open(path, 'w') as f:
            f.write(json.dumps(self._mean_category_number) + '\n')
            f.write(json.dumps(self._category_coefficient) + '\n')
            f.write(json.dumps(self._categories) + '\n')
            f.write(json.dumps(self._labels) + '\n')
            f.write(json.dumps(self._features))
        logger.info('Calculated training data stored into %s.' % path)

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
        return [[i, step] if i + step < max_num else [i, max_num - i] for i in range(0, max_num, step)]

    def _display_progress(self, title, max_progress, progress_queue):
        from progress.bar import Bar
        bar = Bar(title, suffix='%(index)d / %(max)d, %(percent)d%%', max=max_progress)
        cur = 0
        while cur != max_progress:
            progress = progress_queue.get()
            for _ in range(progress):
                bar.next()
            cur += progress
        bar.finish()

    def _calculate_global_parameters(self):
        import multiprocessing as mp

        # initialize the queues
        result_queue = mp.Queue()
        progress_queue = mp.Queue()

        # radius to calculate features
        r = 200

        # calculate global parameters
        total_num = self._database.get_total_num()

        def calculate_local_parameters(db_path, part, neighbor_category, categories, result_queue, progress_queue):
            # initialize local matrix
            mean_category_number = {}
            k_suffixes = {}
            for outer, _ in categories.items():
                mean_category_number[outer] = {}
                k_suffixes[outer] = {}
                for inner, _ in categories.items():
                    mean_category_number[outer][inner] = 0
                    k_suffixes[outer][inner] = 0

            database = Database(db_path)
            # calculate mean category numbers
            for row in database.get_connection().execute(
                            '''SELECT lng,lat,geohash,category FROM \'Beijing-Checkins\' LIMIT %d,%d''' % (
                                    part[0], part[1])):
                neighbors = database.get_neighboring_points(float(row[0]), float(row[1]), r, geo=str(row[2]))
                # calculate mean category number
                for neighbor in neighbors:
                    mean_category_number[neighbor['category']][str(row[3])] += 1

                # calculate category coefficient suffix
                p = str(row[3])
                neighbor_categories = neighbor_category(neighbors)
                sub = (len(neighbors) - neighbor_categories[p])
                
                if sub == 0:
                    progress_queue.put(1)
                    continue

                for l, num in neighbor_categories.items():
                    if num == 0:
                        continue
                    k_suffixes[p][l] += float(num) / sub

                progress_queue.put(1)

            result_queue.put((mean_category_number, k_suffixes))
            return

        # create and start processes
        process_count = mp.cpu_count()
        parts = self._split_range(total_num, int(math.ceil(float(total_num) / process_count)))
        for i in range(process_count):
            process = mp.Process(target=calculate_local_parameters, args=(
                self._database.get_file_path(), parts[i], self._neighbor_categories,
                self._categories, result_queue, progress_queue))
            process.start()

        logger.info('Starting {} processes.'.format(process_count))

        self._display_progress('Calculating global parameters', total_num, progress_queue)

        logger.info('Processes finished.')

        # retrieve and merge the results
        for _ in range(process_count):
            mean_category_number, k_suffixes = result_queue.get()
            for p, _ in self._categories.items():
                for l, _ in self._categories.items():
                    # merge mean category number
                    self._mean_category_number[p][l] += mean_category_number[p][l]
                    # TODO: to delete this line of code when we run training in full dataset
                    self._category_coefficient[p][l] += k_suffixes[p][l]

        # subsequent calculations
        for p, _ in self._categories.items():
            for l, _ in self._categories.items():
                if self._categories[l] == 0:
                    continue
                self._mean_category_number[p][l] /= self._categories[l]

                k_prefix = float(total_num - self._categories[p]) / (self._categories[p] * self._categories[l])
                self._category_coefficient[p][l] *= k_prefix

    def _calculate_features(self):
        import multiprocessing as mp

        # initialize the queues
        result_queue = mp.Queue()
        progress_queue = mp.Queue()

        # radius to calculate features
        r = 200

        # calculate global parameters
        total_num = self._database.get_total_num()

        def calculate_features(db_path, part, vectorize_point, result_queue, progress_queue):
            # initialize local matrix
            labels = []
            features = []

            database = Database(db_path)
            # calculate mean category numbers
            for row in database.get_connection().execute(
                            '''SELECT lng,lat,geohash,checkins FROM \'Beijing-Checkins\' LIMIT %d,%d''' % (
                            part[0], part[1])):
                try:
                    neighbors = database.get_neighboring_points(float(row[0]), float(row[1]), r, geo=str(row[2]))
                    # add feature
                    features.append(vectorize_point(neighbors, '生活娱乐'))
                    # add label
                    labels.append([int(row[3])])
                except Exception as e:
                    print(e)
                finally:
                    progress_queue.put(1)

            result_queue.put((labels, features))
            return

        process_count = mp.cpu_count()
        parts = self._split_range(total_num, int(math.ceil(float(total_num) / process_count)))
        for i in range(process_count):
            process = mp.Process(target=calculate_features, args=(
                self._database.get_file_path(), parts[i], self.vectorize_point, result_queue, progress_queue))
            process.start()

        logger.info('Starting {} processes.'.format(process_count))

        self._display_progress('Calculating features', total_num, progress_queue)

        logger.info('Processes finished.')

        # retrieve results
        for _ in range(process_count):
            labels, features = result_queue.get()
            self._labels.extend(labels)
            self._features.extend(features)

    def prepare(self, database):
        logger.info('Pre-calculated train file not found, calculating training data...')
        start_time = time.time()
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
        self._calculate_global_parameters()
        self._calculate_features()

        end_time = time.time()
        logger.info('Training data calculated in {} seconds.'.format(end_time - start_time))

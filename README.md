# RankNear [![Build Status](https://www.travis-ci.org/RyanWangGit/ranknear.svg?branch=master)](https://www.travis-ci.org/RyanWangGit/ranknear) [![codecov](https://codecov.io/gh/RyanWangGit/ranknear/branch/master/graph/badge.svg)](https://codecov.io/gh/RyanWangGit/WhatsNear-Backend)

Rank the locations nearby using RankNet.

## Usage

Simply run `python __main__.py [args]` to learn from the train data and starts the HTTP server, the available arguments are listed as follows:

| Argument                   | Description                            | 
| -------------------------- | -------------------------------------- |
| -h, --help                 | Show help message and exit.            |
| -p PORT, --port PORT       | The port to listen on.                 |
| -s SQLITE, --sqlite SQLITE | The SQLite3 database to read from.     |
| -t TRAIN, --train TRAIN    | The training matrix file to read from. |
| -i IP, --ip IP             | The ip to bind on.                     |
| -m MODEL, --model MODEL    | The trained model to read from.        |

## References
\[1] Burges C, Shaked T, Renshaw E, et al. Learning to rank using gradient descent\[C]//Proceedings of the 22nd international conference on Machine learning. ACM, 2005: 89-96.

## License
[MIT](https://github.com/RyanWangGit/WhatsNear-Backend/blob/master/LICENSE).

from tool.crawler import Crawler


def main():
    Crawler.initialize()
    Crawler.process()

if __name__ == '__main__':
    main()

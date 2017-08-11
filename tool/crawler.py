import os
import re
import time
import logging
import smtplib


from csv import writer
from urlparse import parse_qs
from datetime import datetime
from email.mime.text import MIMEText
from configparser import ConfigParser
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication

from torndb import Connection
from bs4 import BeautifulSoup
from selenium import webdriver
from tornado.template import Loader
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, NoSuchElementException

from utils import catching
from proxy_countries import PROXY_COUNTRIES

# Config initialization
config = ConfigParser()
config.read(os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.ini'))

# Logging
LOGLEVEL = int(config.get('logging', 'loglevel'))
log_file = 'load_time_crawler_{0}.log'.format(datetime.now().strftime('%Y.%m.%d-%H.%M.%S'))
log_file = os.path.join(config.get('logging', 'path'), log_file)
# if not os.path.exists(log_file):
#     open(log_file, 'a').close()
logging.basicConfig(level=LOGLEVEL)
log = logging.getLogger('crawler')
log.setLevel(LOGLEVEL)
formatter = logging.Formatter('%(asctime)s [%(pathname)s:%(lineno)d] %(levelname)8s: %(message)s')
handler = logging.NullHandler()
handler.setFormatter(formatter)
log.addHandler(handler)

# Db connection, closes after loading configuration and reconnecting at the end to insert results
db = Connection(config.get('mysql', 'host'),
                config.get('mysql', 'db'),
                config.get('mysql', 'user'),
                config.get('mysql', 'pass'))

# Template loading
root_path = os.path.join(os.path.abspath(os.path.dirname(__file__)))
loader = Loader(root_path)
template = loader.load('results.html')

# Email configurations
gmail_user = config.get('alerts', 'gmail_user')
gmail_password = config.get('alerts', 'gmail_password')
gmail_host = config.get('alerts', 'gmail_host')
gmail_port = int(config.get('alerts', 'gmail_port'))


class Crawler(object):
    """
        Crawler class to fetch websites, injecting specified tags and measuring loading times.
    """
    configuration = None  # Added dummy properties here, so Pycharm won't highlight them as AttributeError
    driver = None  # ChromeDriver property of class Crawler
    timeout = None  # Timeout for loading web pages
    proxy = None  # Proxy client as a property of class Crawler
    server = None  # Proxy server property
    thresholds = None  # Thresholds for loading time
    headers = ['Website', 'Page loading time', 'Preload', 'Layer']  # Headers of an output csv document

    @staticmethod
    @catching
    def initialize():
        """
            Initializes Crawler.configuration and Crawler.thresholds static variables, closes database to prevent
            hanging connections. Throws an error in case of failed configuration load and exits.
        """
        Crawler.configuration = Crawler.get_configurations() or None
        Crawler.get_thresholds()
        # Crawler.server = Server(config.get('chromedriver', 'proxy_bin'))
        # Crawler.server.start()
        # Crawler.proxy = server.create_proxy(params={'httpsProxy': True})
        # Crawler.proxy = Crawler.server.create_proxy()
        db.close()
        if not Crawler.configuration:
            log.error('Something went wrong with initializing crawler. Exiting')
            exit(1)

    @staticmethod
    @catching
    def get_thresholds():
        """
            Fills in Crawler.thresholds from configuration file.
        """
        Crawler.thresholds = {
            'slowdown': float(config.get('threshold', 'slowdown')),
            'preload': float(config.get('threshold', 'preload')),
            '990': float(config.get('threshold', '990')),
            'provider_response': float(config.get('threshold', 'provider_response'))
        }

    @staticmethod
    @catching
    def process():
        """
            Processes websites from database, fills results (time measurements), calculates average loading time,
            creates csv with all results and sends an email to a list of receivers from configuration file.
        """
        results = {}
        for website in Crawler.configuration:
            results[website] = Crawler.test_load_time(website)
        Crawler.calculate_results(results)
        Crawler.store(results)

    @staticmethod
    @catching
    def prepare(website, with_tag=True):
        """
            Prepares ChromeDriver for tests
        :param website: Website page to get country to test on
        :param with_tag: Boolean flag indicates whenever we want to use driver hosts to prevent loading our js
        :return: Returns nothing, as it creates static variable inside Crawler class
        """
        chrome_options = webdriver.ChromeOptions()
        proxy_server = PROXY_COUNTRIES.get(Crawler.configuration[website]['geo'])
        chrome_options.add_argument('--dns-prefetch-disable')
        if proxy_server:
            chrome_options.add_argument('--proxy-server=%s' % proxy_server)
        if not with_tag:
            chrome_options.add_argument('--host-rules=%s' % "MAP %s 127.0.0.1" % ('mapping_placeholder',))
            # Crawler.proxy = Crawler.server.create_proxy()
            # Crawler.proxy.blacklist(".*imonomy.*", 200)
            # chrome_options.add_argument('--proxy-server=%s' % proxy_url)
        # else:
        #     chrome_options.add_argument('--proxy-server=%s' % '198.50.219.239:80')
        # else:
        #     Crawler.proxy.whitelist('tag.imonomy.com', 200)
        Crawler.driver = webdriver.Chrome(config.get('chromedriver', 'path'), chrome_options=chrome_options)
        Crawler.driver.set_page_load_timeout(int(config.get('loading', 'timeout_page_load')))

    @staticmethod
    @catching
    def calculate_results(results):
        """
            Calculates results for initial dictionary, like average/maximum loading time
        :param results: Time measures dictionary for every website
        :return: Returns nothing, as it changes the initial dictionary
        """
        for website in results:
            results[website]['average_with_tag'] = sum(results[website]['with_tag'])/len(results[website]['with_tag'])
            results[website]['average_without_tag'] = sum(results[website]['without_tag'])/len(results[website]['without_tag'])

            results[website]['max_with_tag'] = max(results[website]['with_tag'])
            results[website]['max_without_tag'] = max(results[website]['without_tag'])

            results[website]['average_preload'] = sum(results[website]['preload'])/len(results[website]['preload'])
            results[website]['average_layer'] = sum(results[website]['layer'])/len(results[website]['layer'])
            results[website]['average_990'] = sum(results[website]['990'])/len(results[website]['990'])
            results[website]['average_unit'] = (filter(lambda x: x[0], results[website]['unit']) or None,
                                                sum([x[1] for x in results[website]['unit']]) /
                                                len(results[website]['unit']))

            results[website]['max_preload'] = max(results[website]['preload'])
            results[website]['max_layer'] = max(results[website]['layer'])
            results[website]['max_990'] = max(results[website]['990'])
            results[website]['max_unit'] = max(results[website]['unit'], key=lambda unit: unit[1])

    @staticmethod
    @catching
    def test_load_time_with_tag(website):
        """
            Tests loading page with tag. Workflow is following:
                1) Prepare working webdriver for current website (depends if we need proxy or not)
                2) Initializes time_measures dictionary
                3) Delete cookies and request website page
                4) Measure time by following rules:
                    -- Preload - loading time is from driver.get(page) till 'layer' is present in driver
                    -- Layer - loading time is from 'layer' is present in driver till effective page_view pixel fire
                       (990)
                    -- 990 - loading time is from driver.get(page) till 990 is present in driver
                    -- N provider response is from 'layer' is present and driver has pixel of end of chain (985)
                5) Repeat N times from configuration['scans_number']
        :return: Preload load time list, 990 load time list, with_tag load list, position of imonomy tag,
                 Layer load time list, unit_id load time list (unit_id, load time)
        """
        Crawler.prepare(website)

        time_measures = {'preload': [], 'with_tag': [], '990': [], 'layer': [], 'unit': []}

        log.info('Processing website %s with tag' % website)

        if Crawler.configuration[website]['is_layer_active']:
            tag_lookup_name = '%s' % ('tag_lookup_name_placeholder',)
        else:
            tag_lookup_name = '%s' % ('tag_lookup_name_placeholder',)
        for _ in xrange(Crawler.configuration[website]['scans_number']):

            try:

                # Clear cookies and go to website url
                Crawler.driver.delete_all_cookies()
                start_loading_page = time.time()
                Crawler.driver.get(website)

                # Inject tag and start counting time from executing script
                Crawler.driver.execute_script(Crawler.configuration[website]['script'])
                start_loading_tag = time.time()

                # Init variables for end time
                end_loading_tag = None
                end_loading_990 = None
                end_loading_page = None
                end_loading_unit = None
                end_loading_layer = None
                unit_id = None
                try:
                    wait = WebDriverWait(Crawler.driver, timeout=int(config.get('loading', 'timeout_script')),
                                         poll_frequency=0.1)
                    wait.until(
                        expected_conditions.presence_of_element_located(
                            (By.CSS_SELECTOR, "script[src*='%s']" % (tag_lookup_name,))))
                    end_loading_tag = time.time() - start_loading_tag
                    start_loading_layer = time.time()
                    wait.until(
                        expected_conditions.presence_of_element_located(
                            (By.CSS_SELECTOR, "img[src*='990']")))

                    end_loading_page = time.time() - start_loading_page
                    end_loading_990 = time.time() - start_loading_tag
                    end_loading_layer = time.time() - start_loading_layer
                    log.info('Located layer and effective_page_view pixel. Trying to locate shown pixel (985)')
                except TimeoutException:
                    log.error('Our script took too much time to load')
                    end_loading_page, end_loading_tag, end_loading_990, end_loading_layer = end_loading_page or 100,\
                                                                                            end_loading_tag or 100,\
                                                                                            end_loading_990 or 100,\
                                                                                            end_loading_layer or 100
                except NoSuchElementException:
                    log.error('Our script wasn\'t located in source of web page %s' % (website, ))
                    end_loading_page, end_loading_tag, end_loading_990, end_loading_layer = end_loading_page or 100, \
                                                                                            end_loading_tag or 100, \
                                                                                            end_loading_990 or 100, \
                                                                                            end_loading_layer or 100
                finally:
                    try:
                        unit_id = None
                        end_loading_unit = 0
                        wait.until(expected_conditions.presence_of_element_located(
                            (By.CSS_SELECTOR, "img[src*='ai=985'], img[src*='ai=983']")
                        ))
                        end_loading_unit = time.time() - start_loading_tag
                        unit_id = Crawler.driver.find_element_by_css_selector("img[src*='ai=985'], img[src*='ai=983']")
                        unit_id = parse_qs(unit_id.get_attribute('src'))
                        if unit_id['ai'][0] == '983':
                            log.info('No Adunits because of caps')
                            unit_id = 'Failed on caps'
                        else:
                            unit_id, = unit_id['uid']
                            log.info('Located ad unit %s' % unit_id)
                    except TimeoutException:
                        log.error('No ad units were found on web page because of timeout %s' % (website,))
                    except NoSuchElementException:
                        log.error('Shown wasn\'t located in web page %s' % (website, ))
                    except KeyError:
                        log.error('Error parsing end of chain pixel src')

                time_measures['preload'].append(end_loading_tag)
                time_measures['990'].append(end_loading_990)
                time_measures['with_tag'].append(end_loading_page)
                time_measures['layer'].append(end_loading_layer)
                time_measures['unit'].append((unit_id, end_loading_unit))
            except Exception as e:
                log.error('Error processing %s. Error: %s' % (website, e))
                time_measures['preload'].append(100)
                time_measures['990'].append(100)
                time_measures['with_tag'].append(100)
                time_measures['layer'].append(100)
                time_measures['unit'].append((None, 0))
        Crawler.driver.quit()
        return time_measures

    @staticmethod
    @catching
    def test_load_time_without_tag(website):
        """
                Tests loading time without imonomy tag
        :param website: Website page to test
        :return: Returns list of seconds, which take driver to fully load website page without imonomy tag
        """
        Crawler.prepare(website, with_tag=False)
        time_measures = {'without_tag': [], 'position': False}
        log.info('Processing website %s without tag' % website)
        for _ in xrange(Crawler.configuration[website]['scans_number']):

            # Clear cookies and go to website url
            Crawler.driver.delete_all_cookies()
            start_loading_page = time.time()
            try:
                Crawler.driver.get(website)
            except TimeoutException:
                log.info('Timeout loading webpage %s' % (website,))

            end_loading_page = time.time() - start_loading_page

            time_measures['without_tag'].append(end_loading_page)

        # Position is determined following way:
        # False (Wrong) if preload.js is inside <head></head> or it's not in 30% of end of the <body></body>
        # Otherwise True (OK)
        source = BeautifulSoup(Crawler.driver.page_source, "html.parser")
        source_body = source.body
        try:
            if source.head.find_all(src=re.compile('preload')) or (source_body.find(src=re.compile('preload.js')) and
                                                                   source_body.index(source_body.find(
                                                                       src=re.compile('preload.js')))/float(len(source_body)) < 0.7):

                time_measures['position'] = (False, "Tag is located in <head>")
            else:
                time_measures['position'] = True
        except ValueError:
            log.error('Tag is located in one of <body></body> children. Website %s' % website)
            time_measures['position'] = (False, "Tag is located in one of <body></body> children.")

        Crawler.driver.quit()
        return time_measures

    @staticmethod
    @catching
    def test_load_time(website):
        """
        Method measures loading time for websites with/without imonomy tag.
        Workflow is following:
            1) Open website page we need to run tests on via Selenium
            2) Inject our tag with execute_script function and measure time with callback-based WebDriver.
        :param website:
        :return: time measurement statistics
        """
        log.info('Started processing website %s, it has %s runs' %
                 (website, Crawler.configuration[website]['scans_number']))

        results_with_tag = Crawler.test_load_time_with_tag(website)
        results_without_tag = Crawler.test_load_time_without_tag(website)

        time_measures = {'preload': results_with_tag['preload'],
                         'without_tag': results_without_tag['without_tag'],
                         '990': results_with_tag['990'],
                         'with_tag': results_with_tag['with_tag'],
                         'position': results_without_tag['position'],
                         'layer': results_with_tag['layer'],
                         'unit': results_with_tag['unit']}

        log.info('Finished processing website %s' % website)
        return time_measures

    @staticmethod
    @catching
    def get_configurations():
        res = db.query("QUERY PLACEHOLDER")
        configurations = {
            website['website_page']:
                {
                    'script': website['website_tag'],
                    'scans_number': website['scans_number']-5,
                    'geo': website['geo'],
                    'is_layer_active': website['is_layer']
                }
                for website in res}
        return configurations

    @staticmethod
    @catching
    def store(results):
        log.info('Creating an csv document with results of run')
        output_file = Crawler.create_name()
        with open(output_file, 'w+') as file_handler:
            csv_writer = writer(file_handler, dialect="excel")
            for website in results:
                csv_writer.writerow([website] + range(1, Crawler.configuration[website]['scans_number']+1))
                csv_writer.writerow(['Loading time without tag'] + results[website]['without_tag'])
                csv_writer.writerow(['Loading time with tag'] + results[website]['with_tag'])
                csv_writer.writerow(['Preload loading time'] + results[website]['preload'])
                csv_writer.writerow(['Layer loading time'] + results[website]['layer'])
                csv_writer.writerow(['990 loading time'] + results[website]['990'])
                csv_writer.writerow([])
        Crawler.send_mail(output_file, results)

    @staticmethod
    @catching
    def create_name():
        filename_pattern = config.get('results', 'filename_pattern')
        filename_pattern = filename_pattern.format(datetime.now().__str__().split('.')[0])
        return filename_pattern

    @staticmethod
    @catching
    def generate_template(results):
        return template.generate(results)

    @staticmethod
    @catching
    def send_mail(output_file, results):
        log.info("Sending email with results")
        msg = MIMEMultipart()
        msg['Subject'] = 'Results for automated page loading scan tool on %s' %\
                         (datetime.now().__str__().split('.')[0],)
        to = config.get('results', 'receivers').split(',')
        emailto = ', '.join(to)
        msg['From'] = gmail_user
        msg['To'] = emailto
        msg.preamble = 'Results for automated page loading scan tool'
        html = MIMEText(template.generate(results=results, config=Crawler.configuration,
                                          thresholds=Crawler.thresholds),
                        'html', _charset='utf-8')
        msg.attach(html)
        if not os.path.isfile(output_file):
            log.error('Something went wrong with results file. Aborting')
            return
        with open(output_file, "rb") as file_handler:
            part = MIMEApplication(
                file_handler.read(),
                Name=os.path.basename(output_file)
            )
            part['Content-Disposition'] = 'attachment; filename="%s"' % os.path.basename(output_file)
            msg.attach(part)
        server = smtplib.SMTP(gmail_host, gmail_port)
        server.ehlo()
        server.starttls()
        server.login(gmail_user, gmail_password)
        server.sendmail(gmail_user, to, msg.as_string())
        server.close()



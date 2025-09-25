#!/Users/jialongwangsmacbookpro16/Desktop/chatbot/code/bin/python3

from argparse import ArgumentParser
import csv
import sys
import os
import shutil
import subprocess

from radarclient.model import TestSuite, TestCase, Keyword, SimpleIntegerValueConverter, \
    KeywordListValueConverter, AbstractValueConverter
from radarclient.client import RadarClient, ClientSystemIdentifier, RetryPolicy
from radarclient import __version__ as rc_version
import logging
import logging.handlers
import pprint
import re
from copy import deepcopy, copy
from collections import defaultdict
from typing import List, Tuple, Dict, IO, Generator, Union
from datetime import datetime
import warnings as py_warnings
import time
from pathlib import Path

log_file_path = '/var/tmp/tstt_excel_logs.log'
log_file_formatter = logging.Formatter('%(asctime)s: %(levelname)s: %(module)s: %(message)s')
log_file_handler = logging.handlers.RotatingFileHandler(log_file_path, backupCount=5)
log_file_handler.setLevel(logging.DEBUG)
log_file_handler.setFormatter(log_file_formatter)
log_file_handler.doRollover()

output_formatter = logging.Formatter('%(message)s')
output_handler = logging.StreamHandler(sys.stdout)
output_handler.setLevel(logging.INFO)
output_handler.setFormatter(output_formatter)

# noinspection PyArgumentList
logging.basicConfig(
    level=logging.DEBUG,
    handlers=[log_file_handler, output_handler]
)


class InputHelpers(object):

    @staticmethod
    def get_yes_or_no(question):
        """
        Asks a user a question and keeps prompting for response until getting either a 'y' or 'n'

        :param question: Question to ask the user
        :return: True if 'y', False if 'n'
        """
        response = ''
        while response != 'n' and response != 'y':
            response = input('\n{} (y/n) '.format(question))

        return response == 'y'

    @staticmethod
    def check_dest_file(filepath):
        """
        Checks if a file already exists at a filepath and asks user if they are ok overwriting it

        :param filepath: filepath to check
        :return: True if user is ok overwriting, False otherwise
        """
        to_return = True
        if os.path.exists(filepath):
            to_return = InputHelpers.get_yes_or_no(
                '{} exists. Would you like to overwrite it?'.format(filepath))
        return to_return


class ProcessedCSV(object):
    def __init__(self, starting_position: int):
        self.cases = []
        self.starting_position = starting_position


class TSTTExcel(object):
    DEFAULT_CASE_FIELDS = [
        'suiteId',
        'caseId',
        'title',
        'expectedTimeInSeconds',
        'priority',
        'instructions',
        'data',
        'expectedResult',
        'description'
    ]

    CASE_FIELDS_FOR_FETCHING = copy(DEFAULT_CASE_FIELDS)
    CASE_FIELDS_FOR_FETCHING.pop(0)

    VALUE_CONVERTER_MAP = {
        'caseId': SimpleIntegerValueConverter,
        'priority': SimpleIntegerValueConverter,
        'suiteId': SimpleIntegerValueConverter,
        'keywords': KeywordListValueConverter,
        'expectedTimeInSeconds': SimpleIntegerValueConverter
    }
    CSV_PATTERN = re.compile(r'(\d+)\.csv$')

    BACKUP_DIR = '/var/tmp/tstt_excel_backups/'
    CREATE_KEYWORD = Keyword({'name': 'Created by TSTT Excel', 'id': 1273288})
    UPDATE_KEYWORD = Keyword({'name': 'Updated by TSTT Excel', 'id': 1273289})

    # These cannot be updated through a standard call to the update endpoint of the API
    UNUPDATABLE_CASE_KEYS = {'caseId', 'suiteId'}

    def __init__(self, arguments) -> None:
        self.arguments = arguments
        logging.debug('Arguments were: {}'.format(self.arguments))

        if getattr(self.arguments, 'input_dir', None) is not None:
            self.arguments.input_dir = os.path.expanduser(self.arguments.input_dir)

        self.arguments.no_prompt = getattr(self.arguments, 'no_prompt', False)
        self.client = RadarClient.radarclient_for_current_appleconnect_session(
            ClientSystemIdentifier('tstt_excel', rc_version),
            retry_policy=RetryPolicy()
        )
        self.keyword_cache = {}
        if arguments.verbose:
            output_handler.setLevel(logging.DEBUG)
            output_handler.setFormatter(log_file_formatter)

    @staticmethod
    def replace_for_filepath(to_replace: str) -> str:
        """
        Replaces any characters that need replacing to be used in file path

        :param str to_replace: string to replace characters in

        :return: str with unsafe characters replaced
        """
        replacements = {
            ':': '|',
            ' ': '_',
            '/': '-'
        }
        char_list = []

        for character in to_replace:
            if character in replacements:
                char_list.append(replacements[character])
            else:
                char_list.append(character)

        return ''.join(char_list)

    @staticmethod
    def build_safe_file_name_for_suite(test_suite: TestSuite) -> str:
        """
        Builds a safe file name such that it is <title><suite_id>.csv. Ensures name is 255
        characters or fewer.

        :param TestSuite test_suite: test suite to build filename for

        :return: str filename
        """
        name_end = '{}.csv'.format(test_suite.database_id_attribute_value())
        safe_length = 255 - len(name_end) - 1
        unescaped_name = test_suite.title[0:safe_length] + ' ' + name_end
        return TSTTExcel.replace_for_filepath(unescaped_name)

    @staticmethod
    def decode_row_from_csv(row: dict) -> Dict:
        """
        Decodes a row from CSV

        :param row: row to decode
        :return: decoded dict
        """
        to_return = {}
        for key in row:
            converter = TSTTExcel.VALUE_CONVERTER_MAP.get(key, AbstractValueConverter)
            to_return[key] = converter.decode_csv_value(row[key])
        logging.debug('Decoded row = {}'.format(to_return))
        return to_return

    @staticmethod
    def read_in_from_file(open_file: IO[str]) -> Generator:
        """
        Creates a generator that returns one decoded row of a file at a time

        :param open_file: an already opened CSV to read from

        :return: A generator that reads in and decodes one row at a time from the file
        """
        reader = csv.DictReader(open_file)
        return (TSTTExcel.decode_row_from_csv(row) for row in reader)

    @staticmethod
    def encode_test_case_for_csv(test_case: TestCase) -> Dict:
        """
        Encodes values in a test case for writing to a CSV

        :param test_case: test case to encode
        :return: Dictionary of encoded data
        """
        to_return = {}
        for key in TSTTExcel.DEFAULT_CASE_FIELDS:
            converter = TSTTExcel.VALUE_CONVERTER_MAP.get(key, AbstractValueConverter)
            to_return[key] = converter.encode_csv_value(getattr(test_case, key, None))
        logging.debug('Encoded row = {}'.format(to_return))
        return to_return

    @staticmethod
    def write_suite_to_file(file_path: str, test_suite_cases: List[TestCase]) -> None:
        """
        Writes a list of test cases to a file

        :param file_path: path to output file
        :param test_suite_cases: list of cases to right

        :return: None
        """
        logging.info('Writing data to file')
        # If no cases are passed in, exclude suiteId
        if len(test_suite_cases) == 0:
            headers = [header for header in TSTTExcel.DEFAULT_CASE_FIELDS if header != 'suiteId']
        else:
            headers = TSTTExcel.DEFAULT_CASE_FIELDS
        if not os.path.exists(os.path.dirname(file_path)):
            os.makedirs(os.path.dirname(file_path))
        with open(file_path, 'w') as outfile:
            writer = csv.DictWriter(outfile, headers, extrasaction='ignore')
            writer.writeheader()

            for case in test_suite_cases:
                writer.writerow(TSTTExcel.encode_test_case_for_csv(case))

    @staticmethod
    def build_create_new_request_data(row: Dict) -> Dict:
        """
        Trims down values from a row to just the values that are permitted when creating a new
        test case. It also works around <rdar://problem/66405284>

        :param row: row from CSV to trim
        :return: trimmed row
        """
        request_data = {}
        for key in TestCase.CREATE_AND_ADD_VALID_FIELDS:
            # Workaround <rdar://problem/66405284> Test Suite Case Endpoints Use Both
            # expectedResults and expectedResult
            convert_key = TestCase.REVERSED_REPLACEMENT_KEYS.get(key, key)
            request_data[key] = row.get(convert_key)
        logging.debug('New request data = {}'.format(request_data))
        return request_data

    @staticmethod
    def validate_current_case_order(test_suite: TestSuite, order_list: List) -> bool:
        """
        Validates a suite is in expected order

        :param test_suite: suite to validate
        :param order_list: list containing caseId int or title values. Title values should be
            used for situations in which the test case was created by the csv
        :return: True if in order, False otherwise
        """
        are_equal = len(test_suite.cases) == len(order_list)
        if are_equal:
            for i in range(len(order_list)):
                if isinstance(order_list[i], int):
                    are_equal = order_list[i] == test_suite.cases[i].database_id_attribute_value()
                else:
                    are_equal = order_list[i] == test_suite.cases[i].title

                if not are_equal:
                    break
        return are_equal

    @staticmethod
    def is_item_git_tracked(file_path: str) -> bool:
        """
        Determines if a file or directory is tracked in git

        :param str file_path: path to evaluate

        :return: True if it is, False if it isn't
        """
        base_command = ['git', '-C', 'ls-files', '--error-unmatch']
        if os.path.isfile(file_path):
            base_command.insert(2, os.path.dirname(file_path))
            base_command.insert(4, os.path.basename(file_path))
        else:
            base_command.insert(2, file_path)
        git_check = subprocess.run(base_command, capture_output=True)
        logging.debug('git_check return code was {}'.format(git_check.returncode))
        return git_check.returncode == 0

    @staticmethod
    def get_current_suites_in_tree(dir_path: str) -> Dict[int, str]:
        """
        Walks the directory tree of dir_path and finds all test suite CSV files

        :param str dir_path: path of directory to search through

        :return: dict with suite ID as the key and filepath as the value
        """
        to_return = {}
        for root, dirs, files in os.walk(dir_path):
            for file in files:
                match = re.search(TSTTExcel.CSV_PATTERN, file)
                path = os.path.join(root, file)
                if match is not None:
                    to_return[int(match.groups()[0])] = path
                else:
                    logging.debug('"{}" did not match'.format(path))
        logging.debug('Found the following files: {}'.format(to_return))
        return to_return

    @staticmethod
    def find_case_in_cases(
            ident: Union[int, str], cases: List[TestCase], start: int) -> Union[None, TestCase]:
        """
        Locates a TestCase in a suite by either title or caseId. Returns the TestCase if found,
        otherwise None

        :param ident: int caseId or str title of case to find
        :param cases: list of TestCase object to search
        :param start: starting index. Will search only subset from start index to end of list

        :return: TestCase if found, otherwise None
        """
        if isinstance(ident, int):
            attr_check = 'caseId'
        else:
            attr_check = 'title'
        for case in cases[start:]:
            if getattr(case, attr_check, None) == ident:
                return case

        return None

    def validate_suite_id_ordering(self, suite_id_list: List[int]) -> List[str]:
        """
        Validates that any sub-suite cases are in a single block

        :param suite_id_list: list of suite IDs to validate
        :return: list of string warnings or empty list if no warnings
        """
        to_return = []
        suite_indexes = defaultdict(list)
        for i in range(len(suite_id_list)):
            suite_id = suite_id_list[i]
            if suite_id != self.arguments.suite_id:
                suite_indexes[suite_id].append(i)

        for suite_id in suite_indexes:
            case_num_list = suite_indexes[suite_id]
            if case_num_list != list(range(case_num_list[0], case_num_list[-1] + 1)):
                to_return.append('Suite with ID {} not in sequential order'.format(suite_id))
        logging.debug('suite ID ordering warnings = "{}"'.format(to_return))
        return to_return

    # def evaluate_keywords_for_row(
    #         self, row: Dict[str, List[Keyword]]) -> Tuple[List[int], List[Keyword]]:
    #     """
    #     Evaluates keywords for a row of the CSV file and returns a list of keywords_not_found
    #     and a list of keywords found
    #
    #     :param row: dictionary row to evaluate
    #
    #     :return: list of keyword IDs not found and list of keywords found
    #     """
    #     keywords_not_found = []
    #     keywords_for_row = []
    #
    #     if row.get('keywords') is not None:
    #         keyword_ids = (keyword.id for keyword in row.get('keywords'))
    #         not_found_val = 'Not Found'
    #         for keyword_id in keyword_ids:
    #             keyword = self.keyword_cache.get(keyword_id)
    #             if keyword is None or keyword == not_found_val:
    #                 try:
    #                     keywords = self.client.keywords_for_ids([keyword_id])
    #                 except Exception as ex:
    #                     logging.exception(ex)
    #                     keywords = []
    #                 if len(keywords) == 0:
    #                     keywords_not_found.append(keyword_id)
    #                     self.keyword_cache[keyword_id] = not_found_val
    #                 else:
    #                     keywords_for_row.append(keywords[0])
    #                     self.keyword_cache[keyword_id] = keywords[0]
    #             else:
    #                 keywords_for_row.append(keyword)
    #     logging.debug('keywords_not_found = {}'.format(keywords_not_found))
    #     logging.debug('keywords_for_row = {}'.format(keywords_for_row))
    #     return keywords_not_found, keywords_for_row

    def get_suite_with_cases(self) -> TestSuite:
        """
        Gets the test suite for arguments.suite_id

        :return: TestSuite with detailed case info
        """
        logging.info('Getting test suite with ID {}'.format(self.arguments.suite_id))

        test_suite = self.client.test_suite_for_id(
            self.arguments.suite_id, additional_fields=['associatedTests', 'keywords'])

        if len(test_suite.cases) > 0:
            test_suite.extend_fields_for_all_cases(self.client, fields=self.CASE_FIELDS_FOR_FETCHING)
        return test_suite

    def confirm_export_actions(
            self,
            suites_for_export: List[TestSuite],
            current_files: Dict[int, str]) -> Dict[int, Tuple]:

        to_return = {}

        for suite in suites_for_export:
            current_file_path = current_files.get(suite.database_id_attribute_value())
            export_path = self.build_output_path(suite=suite)
            if current_file_path is None:
                logging.info('Will export suite "{}" and create {}'.format(
                    suite.title, export_path)
                )
                paths = (export_path, None)
            elif current_file_path == export_path:
                logging.info('Will export suite "{}" and overwrite {}'.format(
                    suite.title, current_file_path)
                )
                paths = (current_file_path, None)
            else:
                logging.info(
                    'Will export suite "{}" and overwrite {} and then '
                    'move/rename the file to {}'.format(suite.title, current_file_path, export_path)
                )
                paths = (current_file_path, export_path)
            to_return[suite.database_id_attribute_value()] = paths

        logging.debug('confirm_export_actions returning {}'.format(to_return))
        return to_return

    def build_backup_file_path(self):
        if not os.path.exists(self.BACKUP_DIR):
            os.mkdir(self.BACKUP_DIR)
        current_time = datetime.now().strftime('%Y_%m_%d %H-%M-%S')
        file_name = '{}_{}.csv'.format(self.arguments.suite_id, current_time)
        return os.path.join(self.BACKUP_DIR, file_name)

    def build_output_path(self, suite: TestSuite = None) -> str:
        """
        Build an appropriate output path.

        :return: filepath to export to
        """
        file_name = getattr(self.arguments, 'output_file', None)
        file_dir = os.path.expanduser(self.arguments.output_dir)

        if suite is not None and not self.arguments.no_component:
            file_dir = os.path.join(
                file_dir,
                TSTTExcel.replace_for_filepath(suite.component.name),
                TSTTExcel.replace_for_filepath(suite.component.version)
            )

        if file_name is None:
            if suite is not None:
                file_name = TSTTExcel.build_safe_file_name_for_suite(suite)
            elif getattr(self.arguments, 'suite_id', None) is None:
                file_name = 'default_tstt_excel.csv'
            else:
                file_name = '{}.csv'.format(self.arguments.suite_id)

        if file_name[-4:] != '.csv':
            file_name += '.csv'

        return os.path.join(file_dir, file_name)

    @staticmethod
    def process_csv(path_to_csv: str) -> dict:
        """
        Processes a CSV into a dictionary with suiteIDs as keys storing ProcessedCSV objects

        :param str path_to_csv: path to the location of the CSV

        :return: dictionary of ProcessedCSV objects for each suite in the CSV file. The keys are the suiteIDs
        """
        if not os.path.exists(path_to_csv):
            logging.error('No file exists at {}'.format(path_to_csv))
            sys.exit(1)

        to_return = {}

        with open(path_to_csv, 'r') as infile:
            reader = TSTTExcel.read_in_from_file(infile)
            position = 1

            for row in reader:
                suite_id = row.get('suiteId')

                if suite_id not in to_return:
                    to_return[suite_id] = ProcessedCSV(position)

                to_return[suite_id].cases.append(row)
                position += 1

        return to_return

    def update_a_case(self, test_case: TestCase, csv_row: dict) -> None:
        for k in csv_row:
            if k == 'suiteId' or k == 'caseId':
                pass
            else:
                if csv_row[k] is not None and getattr(test_case, k) != csv_row[k]:
                    test_case.__setattr__(k, csv_row[k])

    def update_suite_and_cases(self, test_suite: TestSuite, info_from_csv: dict = None) -> None:
        if info_from_csv is None:
            info_from_csv = self.process_csv(os.path.expanduser(self.arguments.input_csv))

        test_suite.add_keyword(self.UPDATE_KEYWORD)
        case_ids_in_csv = set()

        # Get top level cases for test suite
        associated_case_ids_set = set()
        for associated_test in test_suite.associatedTests:
            if isinstance(associated_test, TestCase):
                associated_case_ids_set.add(associated_test.database_id_attribute_value())

        test_case_index_dict = {}
        for i in range(len(test_suite.cases)):
            test_case_index_dict[test_suite.cases[i]] = i

        suite_positions_set = set()

        if len(info_from_csv) > 1:
            for suite_id in info_from_csv:
                if suite_id != test_suite.database_id_attribute_value():
                    suite_position = info_from_csv[suite_id].starting_position
                    suite_positions_set.add(suite_position)
                    test_suite.reorder_associated_suite_by_id(suite_id, suite_position)

        placement_index = 1
        while placement_index in suite_positions_set:
            placement_index += 1

        # If suite ID is not found, assume it is the base suite represented by None
        if test_suite.database_id_attribute_value() in info_from_csv:
            info_key = test_suite.database_id_attribute_value()
        else:
            info_key = None

        if info_from_csv.get(info_key) is not None:
            for case_row in info_from_csv[info_key].cases:
                csv_case_id = case_row['caseId']
                case_ids_in_csv.add(csv_case_id)

                if csv_case_id is None:
                    logging.debug('caseID was None. Will create new test and associate it')
                    creation_dict = deepcopy(case_row)
                    if 'suiteId' in creation_dict:
                        del creation_dict['suiteId']
                    del creation_dict['caseId']
                    is_attached, issues = test_suite.create_and_add_test_case(creation_dict, placement_index)
                    if not is_attached:
                        logging.error(f"Could not create new test case with title \"{creation_dict['title']}\" due to "
                                      f"the following issues {issues}. Will stop execution")
                        sys.exit(1)
                elif csv_case_id not in associated_case_ids_set:
                    logging.debug('caseID {} not in test suite, will associate it'.format(csv_case_id))
                    test_suite.add_test_case_by_id(csv_case_id, placement_index)
                else:
                    logging.debug('caseID {} is in test suite. Will reorder and update as needed'.format(csv_case_id))
                    searching_case = (TestCase({'caseId': csv_case_id}))
                    case_index = test_case_index_dict[searching_case]
                    case_to_update = test_suite.cases[case_index]

                    self.update_a_case(case_to_update, case_row)
                    test_suite.reorder_associated_case(case_to_update, placement_index)
                    associated_case_ids_set.remove(case_to_update.database_id_attribute_value())

                placement_index += 1
                # Leave space in ordering for sub-suites
                while placement_index in suite_positions_set:
                    placement_index += 1

        for case_id_to_remove in associated_case_ids_set:
            test_suite.remove_associated_test_case_by_id(case_id_to_remove)

        test_suite.commit_changes(self.client)

        # Handle updates of sub-suites
        for suite_id in info_from_csv:
            if suite_id is not None and suite_id != test_suite.suiteId:
                logging.debug('Starting work on sub-suite {}'.format(suite_id))
                sub_suite = self.client.test_suite_for_id(suite_id, additional_fields=['associatedTests', 'keywords'])
                sub_suite.extend_fields_for_all_cases(self.client, fields=self.CASE_FIELDS_FOR_FETCHING)
                self.update_suite_and_cases(sub_suite, {suite_id: info_from_csv[suite_id]})

    def get_default_csv(self) -> None:
        csv_path = self.build_output_path()
        if not InputHelpers.check_dest_file(csv_path):
            logging.info('Not overwriting file. Exiting.')
            sys.exit(0)

        self.write_suite_to_file(csv_path, [])
        logging.info('Created default file {}'.format(csv_path))

    def create_suite(self) -> TestSuite:
        if not (self.arguments.comp_id or (self.arguments.comp_name and self.arguments.comp_version)):
            comp_arg_error = 'You must include either the --comp_id OR both --comp_name and --comp_version arguments.'
            logging.critical(comp_arg_error)
            sys.exit(1)
        if self.arguments.comp_id:
            component = self.client.component_for_id(self.arguments.comp_id)
        elif self.arguments.comp_name and self.arguments.comp_version:
            component = self.client.component_for_name_and_version(self.arguments.comp_name,
                                                                   self.arguments.comp_version)
        else:
            comp_arg_error = 'Incompatible component arguments provided'
            logging.critical(comp_arg_error)
            raise Exception(comp_arg_error)
        title = self.arguments.suite_title

        suite_data = {'title': title, 'componentId': component.database_id_attribute_value()}
        suite_find = {
            'title': title,
            'component': {
                'id': {'eq': component.database_id_attribute_value()}
            }
        }

        test_suites = self.client.find_test_suites(suite_find)

        # Guard against multiple suites of the same kind being created
        if len(test_suites) > 0:
            logging.info('Suite(s) in component {} with the title {} already exist:'.format(
                component, title
            ))
            for test_suite in test_suites:
                logging.info(str(test_suite))
            if not InputHelpers.get_yes_or_no('Would you like to continue to create a new suite?'):
                logging.info('You can update a suite using the "update" command. For help with '
                             'update, run "tstt_excel.py update --help"')

                # Exit the program completely. Don't remove this or bad things will happen
                sys.exit(0)

        logging.info('Creating test suite with title {} in component {}'.format(title, component))
        created_suite = self.client.create_test_suite(suite_data)
        logging.info('Created suite with suiteId {}\n'.format(created_suite.database_id_attribute_value()))
        self.arguments.suite_id = created_suite.database_id_attribute_value()
        full_suite = self.get_suite_with_cases()
        full_suite.add_keyword(self.CREATE_KEYWORD)
        full_suite.commit_changes(self.client)
        return full_suite

    def export_suite(self, test_suite: TestSuite, output_path: str, new_path: str = None) -> None:
        self.write_suite_to_file(output_path, test_suite.cases)
        print()
        logging.info('Finished exporting "{}" to {}'.format(test_suite.title, output_path))
        if new_path is not None and output_path != new_path:
            if TSTTExcel.is_item_git_tracked(output_path):
                Path.mkdir(Path(os.path.dirname(new_path)), exist_ok=True, parents=True)
                logging.debug('Moving {} with git mv'.format(output_path))
                subprocess.check_output([
                    'git', '-C', self.arguments.output_dir, 'mv', output_path, new_path
                ], stderr=subprocess.STDOUT)
            else:
                logging.debug('Moving {} with shutil'.format(output_path))
                shutil.move(output_path, new_path)
            logging.info('Moved {} to {}'.format(output_path, new_path))

    def export_suites(self) -> None:
        errors = []
        if getattr(self.arguments, 'query', None) is not None:
            logging.info('Pulling all test suites for query {}'.format(self.arguments.query))
            test_suites = self.client.test_suites_for_query(self.arguments.query)
        else:
            logging.info('Getting test suite info')
            test_suites = [self.client.test_suite_for_id(self.arguments.suite_id)]

        current_files = self.get_current_suites_in_tree(self.arguments.output_dir)
        path_info = self.confirm_export_actions(test_suites, current_files)
        if not InputHelpers.get_yes_or_no('Do the above actions appear to be correct?'):
            logging.info('Exiting export')
            sys.exit(0)

        for test_suite in test_suites:
            try:
                self.arguments.suite_id = test_suite.database_id_attribute_value()
                suite_with_cases = self.get_suite_with_cases()
                paths = path_info[test_suite.database_id_attribute_value()]
                self.export_suite(suite_with_cases, paths[0], paths[1])
            except Exception:
                error_str = 'Failed to export {}'.format(test_suite.title)
                logging.exception(error_str)
                errors.append(error_str)

        if len(errors) > 0:
            logging.info('Failed to export the following suites. More details in logs\n{}'.format(
                pprint.pformat(errors))
            )
        print()
        logging.info('Export complete')

    def update_suite(self) -> None:
        """
        Updates the test suite with ID suite_id with the file at input_csv. Before updating,
        a back-up is created

        :return: None
        """
        test_suite = self.get_suite_with_cases()
        backup_path = self.build_backup_file_path()
        self.write_suite_to_file(backup_path, test_suite.cases)
        logging.info('Backup of suite {} created at {}'.format(test_suite.database_id_attribute_value(), backup_path))
        self.update_suite_and_cases(test_suite)
        logging.info('Done updating suite {}'.format(test_suite.database_id_attribute_value()))

    def update_suites(self) -> None:
        """
        Finds all csv files in input_dir that are test suites and updates the suites in TSTT

        :return: None
        """
        errors = []
        suite_csvs = self.get_current_suites_in_tree(self.arguments.input_dir)
        if not self.arguments.no_prompt:
            logging.info('Will perform the following updates')
            for suite_id in suite_csvs:
                logging.info('Will update suite with ID {} to match file {}'.format(
                    suite_id, suite_csvs[suite_id])
                )
            if not InputHelpers.get_yes_or_no('Should these changes be made?'):
                sys.exit(0)

        for suite_id in suite_csvs:
            self.arguments.suite_id = suite_id
            self.arguments.input_csv = suite_csvs[suite_id]

            try:
                self.update_suite()
            except Exception:
                error_string = 'Failed to update suite with ID {}'.format(suite_id)
                logging.exception(error_string)
                errors.append(suite_id)

        if len(errors) > 0:
            logging.info('Failed to update the following suites. More details in logs\n{}'.format(
                pprint.pformat(errors))
            )

    def execute(self) -> None:
        logging.debug('Starting execute')
        logging.debug('Arguments were:\n{}'.format(pprint.pformat(vars(self.arguments))))

        if self.arguments.command == 'get_default_csv':
            self.get_default_csv()
        elif self.arguments.command == 'export':
            if self.arguments.suite_id is None and self.arguments.query is None:
                raise ValueError('Must pass in suite_id or query')
            else:
                self.export_suites()
        elif self.arguments.command == 'create':
            test_suite = self.create_suite()
            csv_info = self.process_csv(os.path.expanduser(self.arguments.input_csv))
            self.update_suite_and_cases(test_suite, csv_info)
        elif self.arguments.command == 'update':
            if self.arguments.no_prompt and not \
                    InputHelpers.get_yes_or_no('Running with no_prompt will overwrite any test suite '
                                               'without any additional input from you. Be sure you '
                                               'know what you are doing. Would you like to continue?'):
                logging.info('Exiting update')
                sys.exit(0)

            if self.arguments.input_dir is not None:
                self.update_suites()
            elif self.arguments.input_csv is not None:
                if self.arguments.suite_id is None:
                    match = re.search(self.CSV_PATTERN, self.arguments.input_csv)
                    if match is None:
                        raise ValueError('If no suite_id provided, input_csv must have a name like '
                                         '"%<suite_id>.csv"')
                    else:
                        self.arguments.suite_id = int(match.groups()[0])

                self.update_suite()
            else:
                raise ValueError('Value for input_dir or input_csv must be passed in to update')
        else:
            raise ValueError('No command found')


def main():
    parent_parser = ArgumentParser(add_help=False)
    parser = ArgumentParser(add_help=True)
    subparsers = parser.add_subparsers(title='actions', help='<command> help',
                                       required=True, dest='command')
    parent_parser.add_argument('-v', '--verbose', dest='verbose', default=False,
                               action='store_true', help='Turn on verbose logging in console')

    export_defaults_parent_parser = ArgumentParser(add_help=False)
    export_defaults_parent_parser.add_argument(
        '-o', '--output_file', dest='output_file',
        help='Output file name for CSV. If not specified, file will be '
             '<suite title>_<suite_id>.csv if exporting a suite, '
             'otherwise it will be default_tstt_excel.csv. If exporting '
             'from a query, this is ignored'
    )
    export_defaults_parent_parser.add_argument('-d', '--output_dir', dest='output_dir',
                                               default=os.getcwd(),
                                               help='Directory to save the file(s) in. Defaults to '
                                                    'current working directory')

    # Export action arguments
    export_parser = subparsers.add_parser(
        'export',
        help='Export existing suite or all suites in a saved query to CSV.',
        description='Exports the suite with ID suite_id or all suites in a saved query. By '
                    'default, suites are saved at the path constructed like '
                    '<output_dir>/<component name>/<component version>/<suiteTitle>_<suiteId>.csv. '
                    'A value for either suite_id or query is required. If query is provided, '
                    'suite_id is ignored.',
        epilog='Note on naming. All spaces will be replaced with "_" and all ":" will be replaced '
               'with "|"',
        parents=[parent_parser, export_defaults_parent_parser]
    )
    export_parser.add_argument('-q', '--saved_query', dest='query', type=int,
                               help='Saved radar query of test suites to export.')
    export_parser.add_argument('-s', '--suite_id', dest='suite_id',
                               help='ID of the suite to export', type=int)
    export_parser.add_argument('--no_component', dest='no_component', default=False,
                               action='store_true',
                               help='Just place file in output_dir, do not add component based '
                                    'sub directories')

    # Create action arguments
    create_parser = subparsers.add_parser(
        'create',
        help='Create a brand new suite from CSV file',
        description='Creates a new test suite from input_csv in '
                    'component comp_name | comp_version with title suite_title',
        parents=[parent_parser]
    )
    create_parser.add_argument('--comp_name', dest='comp_name',
                               help='Name of the component in which the test suite should be '
                                    'created. Use either both --comp_name and --comp_version '
                                    'or only --comp_id.')
    create_parser.add_argument('--comp_version', dest='comp_version',
                               help='Version of the component in which the test suite should be '
                                    'created. Use either both --comp_name and --comp_version '
                                    'or only --comp_id.')
    create_parser.add_argument('--comp_id', dest='comp_id',
                               help='ID of the component in which the test suite should be '
                                    'created. Use either both --comp_name and --comp_version '
                                    'or only --comp_id.')
    create_parser.add_argument('--suite_title', dest='suite_title', required=True,
                               help='Title of suite to be created')
    create_parser.add_argument('-i', '--input_csv', dest='input_csv', required=True,
                               help='Path to the CSV to use for creating test suite')

    # Update actions arguments
    update_parser = subparsers.add_parser(
        'update',
        help='Update an existing suite from CSV. Backup of current suite will be '
             'created at {}'.format(TSTTExcel.BACKUP_DIR),
        description='Update an existing test suite. suite_id must be provided unless the name of '
                    'input_csv is like "%%suite_id>.csv"',
        parents=[parent_parser]
    )
    update_parser.add_argument('-s', '--suite_id', dest='suite_id',
                               help='ID of the suite to update', type=int)
    update_parser.add_argument('-i', '--input_csv', dest='input_csv',
                               help='Path to the CSV to use for updating a test suite')
    update_parser.add_argument('--input_dir', dest='input_dir',
                               help='Script will walk the entire directory tree starting at this '
                                    'directory and files like "%%<suite_id>.csv", '
                                    'extract the suite_id, and update the suite with the '
                                    'matching csv file')
    update_parser.add_argument('--no_prompt', dest='no_prompt', default=False, action='store_true',
                               help='Potentially dangerous. When passed in, dry run of update is '
                                    'skipped and update simply runs')

    # Get defaults action arguments
    subparsers.add_parser('get_default_csv',
                          parents=[parent_parser, export_defaults_parent_parser],
                          help='Create a file with the needed headers to '
                               'start creating a test suite')

    tstt_excel = TSTTExcel(parser.parse_args())
    tstt_excel.execute()


if __name__ == "__main__":
    main()

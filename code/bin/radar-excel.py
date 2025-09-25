#!/Users/jialongwangsmacbookpro16/Desktop/chatbot/code/bin/python3

# TODO / feature requests
# 
# - omniplan support
#   - And if when you import csv to omniplan, if it can take dependencies 
#   - And two, what the output of Omniplan to Csv looks like
#
#   - Test plan for {Milestone} - {Parent Title} - {Tentpole} - {Category}
#   - Description copy of title

from __future__ import absolute_import
from __future__ import unicode_literals
from __future__ import division
from __future__ import print_function

import re
import sys
import os
import io
import yaml
import random
import pprint
import logging
import contextlib
import datetime
import csv
import collections
import unicodedata
import radarclient
from radarclient.model import Category, Tentpole, Event, Relationship, OtherRelatedItem, Person
from radarclient.radartool import AbstractSubcommand, RadarToolCommandLineDriver, ANSIColor

def split_comma(value):
    return re.split(r'\s*,\s*', value)

def get_all_leaf_subclasses(cls):
    for subclass in cls.__subclasses__():
        if subclass.__subclasses__():
            for subclass2 in get_all_leaf_subclasses(subclass):
                yield subclass2
        else:
            yield subclass

def create_handler(configuration, parent_class, *args, **kwargs):
    known_handler_classes = {c.__name__: c for c in get_all_leaf_subclasses(parent_class)}
    if 'handler' not in configuration:
        raise Exception('Configuration is missing "handler" entry: {}'.format(configuration))
    handler_name = configuration['handler']
    if handler_name not in known_handler_classes:
        raise Exception('Unknown handler class {}'.format(handler_name))
    handler_class = known_handler_classes[handler_name]
    return handler_class(configuration, *args, **kwargs)

def unicode_csv_reader(raw_input_file, **kwargs):
    def unicode_file_wrapper(file_obj):
        for row in file_obj:
            row = radarclient.compat.file_output_representation_for_unicode_string(row)
            yield row
    csv_reader = csv.reader(unicode_file_wrapper(raw_input_file), **kwargs)
    for row in csv_reader:
        yield [radarclient.compat.unicode_string_for_argument_string(cell) for cell in row]

def unicode_encode_row(row):
    return [radarclient.compat.file_output_representation_for_unicode_string(radarclient.compat.unicode_string_type(value)) for value in row]

SkipRowDecision = collections.namedtuple('SkipRowDecision', ['should_skip', 'reason'])
FixupTask = collections.namedtuple('FixupTask', ['row_number', 'fixup_handler'])


class FieldHandlerContext(object):

    def __init__(self, datasource, field_map, radar_client, dry_run):
        self.datasource = datasource
        self.field_map = field_map
        self.radar_client = radar_client
        self.dry_run = dry_run
    
    def row_for_row_number(self, row_number):
        return self.datasource.row_for_row_number(row_number)
    
    def is_known_field_name(self, name):
        return name in self.field_map
    
    def radar_id_for_row_number(self, row_number):
        for handler in self.datasource.field_handlers.values():
            if not handler.can_provide_radar_id_for_row():
                continue
            row = self.row_for_row_number(row_number)
            return handler.radar_id_for_row(row)
    
    def value_for_field_and_row_number(self, field_name, row_number):
        return self.row_for_row_number(row_number)[self.field_map[field_name]]


class ValueTransformer(object):

    def __init__(self, configuration=None):
        self.configuration = configuration
        self.parse_configuration()
    
    def parse_configuration(self):
        pass

    def transform(self, value, row, field_map):
        return value


class ConvertToInteger(ValueTransformer):
    """
    This handler lets you convert the data type of a value to integer, to
    convert strings to numbers for the properties where the Radar API expects
    that.

    The SimpleProperty handler automatically adds this transformer for a few
    common properties such as priority, so you usually don't have to specify it.
    It is available for you to add explicitly for cases where the built-in list
    of known integer properties is incomplete.

    Configuration options: none

    Output: Attribute value as integer
    
    Example::

        handler: SimpleProperty
        property: priority
        transform:
            - handler: ConvertToInteger
            
    """

    def transform(self, value, row, field_map):
        if (value is None) or (value == ""):
            return value
        try:
            return int(value)

        except Exception as e:
            print('ConvertToInteger: Error while trying to transform input value {} to integer: {} '.format(value, e), file=sys.stderr)
            raise


class GetAttributeValue(ValueTransformer):
    """
    This handler lets you extract a radar attribute value. An attribute can be either:

    - a property of an object
    - a key in a dictionary
    - the name of a method of an object
    - a list item
    
    Configuration options:

    name
      Attribute's name. Can be a period-separated string to drill into radar object hierarchy e.g. assignee.email
    
    args (optional)
      Arguments of the method (if required, when method shall be invoked)
    
    kwargs (optional)
      Keyword arguments of the method (if required, when method shall be invoked)
    
    index (optional)
      index of the list item to be extracted (shall be used when a specific item in a list is needed)
      If omitted - the whole array will be returned (string with '\\n' between list items)

    Output: Attribute value in string format
    
    Example::

        handler: SimpleProperty
        property: diagnosis
        transform:
            - handler: GetAttributeValue
              name: items
              kwargs: {'type':'user'}
              index: -1
    
    This example provides the *last* user radar "diagnosis" entry i.e. returns the last element of the user "diagnosis" list.
        
    """

    def parse_configuration(self):
        self.name = self.configuration.get('name', None)
        self.kwargs = self.configuration.get('kwargs', {})
        self.args = self.configuration.get('args', [])
        self.index = self.configuration.get('index', None)

        if self.name == None:
            raise Exception('GetAttributeValue transformer requires "name" configuration entry')

    def transform(self, value, row, field_map):
        none_value = ''
        name_path = self.name.split('.')
        parent_value = None
        name_path_element = None
        
        while name_path and value is not None:
            name_path_element = name_path.pop(0)
            parent_value = value
            if isinstance(parent_value, dict):
                value = parent_value.get(name_path_element, none_value)
                if (radarclient.compat.unicode_string_type(name_path_element) not in parent_value):
                    print('Warning: GetAttributeValue could not find key name {} in dictionary {}'.format(name_path_element, parent_value))
            else:
                value = getattr(parent_value, name_path_element, none_value)
                if not hasattr(parent_value, name_path_element):
                    print('Warning: GetAttributeValue could not find attribute name {} in parent {}'.format(name_path_element, parent_value))
            if callable(value):
                value = value(*self.args, **self.kwargs)

        if hasattr(parent_value, 'encode_user_friendly_property_value'):
            value = parent_value.encode_user_friendly_property_value(name_path_element, value)

        if isinstance(value, list):
            if self.index == None:
                value = '\n'.join([radarclient.compat.unicode_string_type(i) for i in value])
            elif value:
                value = radarclient.compat.unicode_string_type(value[int(self.index)])
            else:
                value = none_value
        elif value is None:
            value = none_value
        else:
            value = radarclient.compat.unicode_string_type(value)

        return value


class Template(ValueTransformer):
    """
    This handler lets you combine the values from multiple columns of the
    current row into a string. It takes a template with placeholders
    that are replaced with the various values.

    Example::

        Title:
            handler: SimpleProperty
            property: title
            transform:
            - handler: Template
              template: "{Hierarchy}: {Title}"

    This example combines two spreadsheet columns named "Hierarchy" and "Title".
    In the hypothetical input spreadsheet for this example, the "Hierarchy" column
    contains values such as "TLF", "Sub-TLF" etc. and the resulting title string would
    be something like "Sub-TLF: Radar Title goes here".
        
    """

    def parse_configuration(self):
        self.template = radarclient.compat.unicode_string_for_argument_string(self.configuration['template'])

    def transform(self, value, row, field_map):
        row_dict = {name: row[i].strip() for name, i in field_map.items()}
        try:
            return self.template.format(**row_dict)
        except KeyError as e:
            raise Exception('Template string "{}" references unknown column name: "{}"'.format(self.template, e.args[0]))


class Replace(ValueTransformer):
    """
    This handler lets you replace strings in the input value.

    Example::

        Priority:
            handler: SimpleProperty
            property: priority
            transform:
            - handler: Replace
              search: P
              replace: ""

    This example is used to replace strings such as "P1", "P2" into "1", "2",
    which are then valid values for the priority property.
        
    """

    def parse_configuration(self):
        self.search = self.configuration.get('search', None)
        self.replace = self.configuration.get('replace', None)
        if self.search == None or self.replace == None:
            raise Exception('Replace transformer requires "search" and "replace" configuration entries')

    def transform(self, value, row, field_map):
        return value.replace(self.search, self.replace)


class Date(ValueTransformer):
    """
    This handler lets you parse dates according to a pre-defined format
    and convert them into the ISO8601 format expected by Radar.

    Configuration options:
    
    format
      Defines the date format used in the CSV file.
      Accepts strftime() string codes as detailed in:
      https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior
    
    radar_to_csv (optional)
      boolean flag to indicate the date reading is from radar database to csv (true).
      If omitted (or set to false), the date reading is from csv (and to the radar database)
    
    Example::

        TargetDate:
            handler: SimpleProperty
            property: targetCompletionCurrent
            transform:
                - handler: Date
                  format: "%m/%d/%y"
    
    This example is used to convert dates from "mm/dd/yy" format to ISO 8601 (YYYY-MM-DD) and to set the radar property "targetCompletionCurrent" accordingly.
        
    """

    def parse_configuration(self):
        self.format = self.configuration.get('format', None)
        if self.format == None:
            raise Exception('Date transformer requires "format" configuration entry e.g. %m/%d/%y')
        
        self.radar_to_csv = bool(self.configuration.get('radar_to_csv', False))

    def transform(self, value, row, field_map):
        if value == "":
            return value
        try:
            if self.radar_to_csv:
                parsedDate = value.strftime(self.format)
            else:
                parsedDate = datetime.datetime.strptime(value, self.format).date()
                
        except Exception as e:
            print('Error while trying to parse input value {} to date format {}: {} '.format(value, self.format, e), file=sys.stderr)
            raise
            
        return parsedDate


class Split(ValueTransformer):
    """
    This handler lets you split a given string based on a delimiter and pick out the desired part

    Configuration options:
    
    delimiter
        Substring to look for and split the target string around it
           
    index
        Item number (integer) to be returned after the split, starting with 0.
           
    retainDelimiter (optional)
        boolean flag to indicate if should add the delimiter to the start of the returned string.
        When set to true, it adds the delimiter. When omitted or set to false, it removes the delimiter.
           
    dropIfNoDelimiter (optional)
        boolean flag to indicate if should drop or pick the (whole) string in case there's no delimiter.
        When set to true, it drops the string. When omitted or set to false it, it picks the string

    Example::

        Priority:
            handler: SimpleProperty
            property: diagnosis
            transform:
            - handler: GetAttributeValue
              name: items
              kwargs: {'type':'user'}
            - handler: Split
              delimiter: "<DiagnosisEntry"
              index: 1
              retainDelimiter: True
              dropIfNoDelimiter: True
              
    This example extracts the first user entry from the diagnosis field in radar database.
        
    """

    def parse_configuration(self):
        self.delimiter = self.configuration.get('delimiter', None)
        self.index = self.configuration.get('index', None)
        self.retainDelimiter = self.configuration.get('retainDelimiter', False)
        self.dropIfNoDelimiter = self.configuration.get('dropIfNoDelimiter', False)
        if self.delimiter == None or self.index == None:
            raise Exception('Split transformer requires "delimiter" and "index" configuration entries')
        if not isinstance(self.retainDelimiter, int):
            raise Exception('Split transformer requires "retainDelimiter" to be bool value (True or False)')
        if not isinstance(self.dropIfNoDelimiter, int):
            raise Exception('Split transformer requires "dropIfNoDelimiter" to be bool value (True or False)')

    def transform(self, value, row, field_map):
        splitStr = value.split(self.delimiter)
        splitIndexStr = ""
        if (len(splitStr) > self.index and len(splitStr) >= -self.index ):
            splitIndexStr = splitStr[self.index]
            if (len(splitStr) > 1):
                if self.retainDelimiter:
                    splitIndexStr = self.delimiter + splitIndexStr
            else:
                if self.dropIfNoDelimiter:
                    splitIndexStr = ""
        return splitIndexStr.strip()


class FieldHandler(object):

    def __init__(self, configuration, field_name, column_index, context):
        self.configuration = configuration
        self.field_name = field_name
        self.column_index = column_index
        self.context = context
        self.setup_value_transformers()
        self.parse_configuration()
        self.skip_update_current_radar = False
    
    def setup_value_transformers(self):
        self.value_transformers = None
        if 'transform' not in self.configuration:
            return

        for config in self.configuration['transform']:
            self.add_value_transformer(create_handler(config, ValueTransformer))
    
    def add_value_transformer(self, transformer):
        if not self.value_transformers:
            self.value_transformers = []
        self.value_transformers.append(transformer)

    def has_value_transformer_of_type(self, target_type):
        if self.value_transformers:
            for vt in self.value_transformers:
                if isinstance(vt, target_type):
                    return True
        return False

    def value(self):
        value = self.raw_value
        if not self.value_transformers:
            return value
        for transformer in self.value_transformers:
            value = transformer.transform(value, self.row, self.context.field_map)
        return value

    def set_current_row(self, row, value, row_number):
        self.row = row
        self.row_number = row_number
        self.raw_value = value
        self.update_for_current_row()

    def update_for_current_row(self):
        pass

    def should_skip_current_item(self):
        return SkipRowDecision(False, None)

    def parse_configuration(self):
        pass

    def update_new_radar_data(self, new_radar_data):
        pass

    def set_current_radar_and_id(self, radar, radar_id):
        self.radar = radar
        self.radar_id = radar_id

    def update_current_radar(self, update_identical_values=False):
        #Enables a daughter class to skip "update_current_radar" (skipped once, on every setting of "skip_update_current_radar" to True)
        if self.skip_update_current_radar:
            self.skip_update_current_radar = False
            return

        for property in self.affected_radar_property_names():
            property_data = self.get_property_data(property)
            if not update_identical_values and self.is_radar_data_identical(property, property_data):
                logging.debug('Not updating {} of radar {} because value is identical'.format(property, self.radar_id))
                return
            if self.has_value_transformer_of_type(ConvertToInteger) and (property_data is None or property_data == ""):
                logging.debug('Not updating {} of radar {} because integer value is empty'.format(property, self.radar_id))
                return
            if self.context.dry_run:
                if property_data:
                    print('dry run: would update {} of radar {} to "{}"'.format(property, self.radar_id, self.value()))
            else:
                if property_data and self.radar:
                    setattr(self.radar, property, property_data)
                else:
                    logging.debug('Not updating {} of radar {} because value is empty'.format(property, self.radar_id))
                    
    def get_property_data(self, property):
        value = self.value()
        if isinstance(value, str) and value.isnumeric():
            value = int(value)
        return value

    def get_radar_data(self, property):
        radar_data = None
        if self.radar:
            radar_data = getattr(self.radar, property)
        return radar_data

    def is_radar_data_identical(self, property, property_data):
        if  property_data and radarclient.compat.unicode_string_type(property_data) == radarclient.compat.unicode_string_type(self.get_radar_data(property)):
            return True
        return False

    def assignee_dsid(self):
        return None
    
    def can_provide_radar_id_for_row(self):
        return False
    
    def radar_id_for_row(self, row):
        raise Exception('Handler {} cannot provide Radar ID for row'.format(self))
    
    def update_row_for_csv_export(self, row_dict, row_number):
        return False
    
    def preflight_will_skip_for_row_value(self, value):
        return False

    def preflight_start(self):
        pass

    def preflight_row_value(self, value, row_number, will_skip):
        pass

    def preflight_end(self):
        pass
    
    def affected_radar_property_names(self):
        return set()
    
    def __str__(self):
        return '<Handler "{}" for column "{}">'.format(type(self).__name__, self.field_name)


class Component(FieldHandler):
    """
    This lets you assign the new Radar to a component upon creation.

    The values in the spreadsheet can be in two forms:

    The "Component | version" notation
        In this case, the configuration needs no additional parameters.

        Example::

            Component Name:
                handler: Component

    Any other string
        In this case, you have to map the strings to component names and versions
        in the configuration with one of more matching rules.

        Example::

            Component Name:
                handler: Component
                component_mapping:
                    - match: python-radarclient
                      component_name: python-radarclient
                      component_version: 1.0
                    - match: pmm
                      component_name: Photos Media Mining
                      component_version: all

    You can combine the two forms. The tool first attempts to apply the matching
    rules and if that doesn't work it tries to interpret the value acording to the
    first variant. You could use the second variant to create convenient shortcuts
    for example.

    """

    def preflight_start(self):
        self.known_components = {}

    def preflight_row_value(self, value, row_number, will_skip):
        if will_skip or value in self.known_components:
            return
        component_data = None
        mappings = self.configuration.get('component_mapping', [])
        for mapping in mappings:
            if re.match(mapping['match'], value):
                component_data = {'name': mapping['component_name'], 'version': mapping['component_version']}
        if not component_data:
            items = re.split(r'\s*\|\s*', value)
            if len(items) == 2:
                component_data = {'name': items[0], 'version': items[1]}
            elif re.match(r'\d+$', value):
                component_data = {'id': int(value)}

        if not component_data:
            raise Exception('Missing component value in column "{}" of input row number {}, neither "Component | Version" format nor custom matching rules.'.format(self.field_name, row_number))
        # search based on component id
        if ('id' in component_data):
            components = self.context.radar_client.find_components(component_data)
        # search based on component name and version
        else:
            # Note: "find_components" method returns all components with names that contain the searched "name" string hence
            # will cause an exception when more than one componenet is found. "componens_for_name" method is a better
            # option since it returns a component strictly matching the searched name and version strings
            components = self.context.radar_client.components_for_name(component_data['name'], component_data['version'])
        logging.debug('Looked up components for {}: {}'.format(component_data, components))
        if len(components) != 1:
            raise Exception('Unable to uniquely resolve component {} | {} for value "{}" in column "{}" on row {}, components found: {}'.format(component_data['name'], component_data['version'], value, self.field_name, row_number, components))
        self.known_components[value] = components[0].id

    def update_new_radar_data(self, new_radar_data):
        self.skip_update_current_radar = True
        component_id = self.known_components[self.value()]
        new_radar_data.update({'componentID': component_id})
        
    def get_property_data(self, property):
        property_data = None
        value = self.value()
        if value:
            property_data=self.known_components[value]
        return property_data

    def is_radar_data_identical(self, property, property_data):
        radar_data = self.get_radar_data(property)
        if radar_data and property_data and (radar_data == property_data):
            return True
        return False

    def update_row_for_csv_export(self, row, row_number):
        component_obj = getattr(self.radar, 'component')
        row[self.column_index] = component_obj['name'] + "|" + component_obj['version']
        return True
    
    def affected_radar_property_names(self):
        return {'componentID'}


class ProgramManagementComponentFieldHandler(FieldHandler):

    def get_property_data(self, property):
        property_data = None
        value = self.value()
        value_class = self.value_class()
        if value and value_class:
            property_data = value_class({'name': value})
        return property_data

    def is_radar_data_identical(self, property, property_data):
        radar_data = self.get_radar_data(property)
        if radar_data and property_data and self.get_name(property_data) == getattr(radar_data, 'name'):
            return True
        return False
        
    def update_row_for_csv_export(self, row, row_number):
        property = getattr(self.radar, self.property_name())
        row[self.column_index] = radarclient.compat.unicode_string_type(getattr(property, 'name')) if property else ""
        return True

    def affected_radar_property_names(self):
        return {self.property_name()}

    def get_name(self, property_data):
        return radarclient.compat.unicode_string_type(getattr(property_data, 'name'))


class Category(ProgramManagementComponentFieldHandler):
    """
    This lets you set the Radar's category.

    Example::

        Category:
            handler: Category

    """

    def property_name(self):
        return 'category'
    
    def value_class(self):
        return radarclient.model.Category


class Tentpole(ProgramManagementComponentFieldHandler):
    """
    This lets you set the Radar's tentpole.

    Example::

        Tentpole:
            handler: Tentpole

    """

    def property_name(self):
        return 'tentpole'
    
    def value_class(self):
        return radarclient.model.Tentpole


class Event(ProgramManagementComponentFieldHandler):
    """
    This lets you set the Radar's Event.

    Example::

        Event:
            handler: Event

    """

    def property_name(self):
        return 'event'
    
    def value_class(self):
        return radarclient.model.Event

class Milestone(ProgramManagementComponentFieldHandler):
    """
    This lets you set the Radar's Milestone.

    Example::

        Milestone:
            handler: Milestone

    """

    def property_name(self):
        return 'milestone'
    
    def value_class(self):
        return dict

    def get_name(self, property_data):
        return radarclient.compat.unicode_string_type(property_data.get('name'))
        
class EventAndWeekCode(FieldHandler):
    """
    This lets you set the Radar's Event and target completion date with
    a succinct string of the form "MxWy", e.g. M2W3. For this to work,
    the component the Radar is created in needs to have matching events whose
    names start with M1, M2 etc.

    The tool queries the start dates of those events and adds the week offset
    and sets the target completion date to the Friday of that week. So if the
    value is M1W3 and the M1 event has a start date of x, which should be a
    Monday, then the tool will add 3 weeks to that date (landing on another Monday)
    and then subtract three days to land on the Friday of the third week.

    Since the "M1" etc. events are not unique and they repeat for every release,
    you have to filter them with a regular expression as shown below.

    Example::

        MxWy:
            handler: EventAndWeekCode
            event_component_name: SWE
            event_component_version: All
            event_name_filter: .*Yukon$

    """

    def parse_configuration(self):
        component_name = self.configuration.get('event_component_name', None)
        component_version = self.configuration.get('event_component_version', None)
        event_name_filter = self.configuration.get('event_name_filter', None)
        if not (component_name and component_version and event_name_filter):
            raise Exception('EventAndWeekCode field handler requires event_component_name, event_component_version, and event_name_filter entries')

        components = self.context.radar_client.components_for_name(component_name, component_version)
        if len(components) != 1:
            raise Exception('Unable to uniquely resolve component {} | {}'.format(component_name, component_version))
        component = components[0]
        events = self.context.radar_client.events_for_component(component)
        if not events:
            raise Exception('Unable to find any events for component')
        self.events = [e for e in events if re.match(event_name_filter, e.name)]
        if not self.events:
            raise Exception('Unable to find any events matching filter criteria')

    def preflight_start(self):
        self.code_mapping = {}

    def preflight_row_value(self, value, row_number, will_skip):
        if not value or value in self.code_mapping:
            return

        match = re.match(r'(M\d+)W(\d+)', value)
        if not match:
            raise Exception('Invalid event+week code {}, expected MxWy'.format(value))
        
        event_prefix = match.group(1)
        week_number = int(match.group(2))
        events = [event for event in self.events if event.name.startswith(event_prefix)]
        if len(events) != 1:
            raise Exception('Unable to find unique event starting with {}: {}'.format(event_prefix, events))
        event = events[0]
        target_date = event.beginsAt + datetime.timedelta(weeks=week_number) - datetime.timedelta(days=3)
        self.code_mapping[value] = event, target_date

    def update_current_radar(self, update_identical_values=False):
        if not self.value():
            return

        event, target_date = self.code_mapping[self.value()]
        if self.context.dry_run:
            print(u'dry run: would set event of radar {} to "{}", completion date to "{}"'.format(self.radar_id, event, target_date))
        else:
            self.radar.event = event
            self.radar.targetCompletionCurrent = target_date
    
    def affected_radar_property_names(self):
        return {'event', 'targetCompletionCurrent'}


class Keyword(FieldHandler):
    """
    This lets you add keywords to the Radar.

    The value in the spreadsheet is a comma-separated list of keywords.

    Example::

        Keywords:
            handler: Keyword
    
    If the keywords are ambiguous, you must additionally provide a component
    name and version to restrict the search::

        Keywords:
            handler: Keyword
            keyword_component_name: Camera & Photos
            keyword_component_version: all

    """

    def parse_configuration(self):
        self.search_component = None
        name = self.configuration.get('keyword_component_name', None)
        version = self.configuration.get('keyword_component_version', None)
        if name and version:
            self.search_component = {
                'name': name,
                'version': version,
            }
        
    def preflight_start(self):
        self.keyword_mapping = {}

    def preflight_row_value(self, value, row_number, will_skip):
        if not value or value in self.keyword_mapping:
            return

        keyword_ids_or_names = split_comma(value)
        for keyword_id_or_name in keyword_ids_or_names:
            if re.match(r'^(\d+)$', keyword_id_or_name):
                keywords = self.context.radar_client.keywords_for_ids([keyword_id_or_name])
                logging.debug('Searched for keyword with ID {}, results: {}'.format(keyword_id_or_name, keywords))
            else:
                keywords = self.find_keywords_by_name(keyword_id_or_name)
            if len(keywords) != 1:
                raise Exception('Unable to find unique keyword for search criteria "{}" in row {}. Please use keyword ID instead. Result: {}'.format(search, row_number, keywords))
            self.keyword_mapping[keyword_id_or_name] = keywords[0]

    def update_current_radar(self, update_identical_values=False):
        value = self.value()
        if not value:
            return
        keywords = [self.keyword_mapping[x] for x in split_comma(value)]
        logging.debug('Adding keywords {} to radar {}'.format(keywords, self.radar_id))
        for keyword in keywords:
            if update_identical_values or not self.is_radar_data_identical(keyword):
                if self.context.dry_run:
                    print('dry run: Would add keyword "{}" to radar {}'.format(keyword, self.radar_id))
                else:
                    self.radar.add_keyword(keyword)
            else:
                logging.debug('Keyword {} already exists in radar {}'.format(keywords, self.radar_id))

    def is_radar_data_identical(self, keyword):
        if self.radar is not None:
            for kw in self.radar.keywords():
                if kw.id == keyword.id: return True
        return False

    def update_row_for_csv_export(self, row, row_number):
        keyword_name_list = []
        for kw in self.radar.keywords():
            keyword_name = radarclient.compat.unicode_string_type(getattr(kw, 'name'))
            keyword_id = radarclient.compat.unicode_string_type(getattr(kw, 'id'))
            keywords = self.find_keywords_by_name(keyword_name)
            if len(keywords) != 1:
                keyword_name_list.append(keyword_id)
            else:
                keyword_name_list.append(keyword_name)
        row[self.column_index] = ', '.join(keyword_name_list)
        return True
        
    def find_keywords_by_name(self, keyword_name):
        search = {'name': keyword_name}
        if self.search_component:
            search['component'] = self.search_component
        keywords = self.context.radar_client.find_keywords(search)
        keywords = [kw for kw in keywords if kw.name == keyword_name]
        logging.debug('Searched for keyword with search criteria {}, result: {}'.format(search, keywords))
        return keywords
   
   
class RadarID(FieldHandler):
    """
    The RadarID column handler provides two features:

    1. If the tool encounters a value (any value, it doesn't have to look like a Radar ID)
       in this column for a given row, then it will not create a Radar. It is assumed
       that the Radar already exists or that you want to skip this row.
    2. If there isn't a value in this column and a Radar did get created for the row,
       then the tool will write out a copy of the input spreadsheet CSV with the Radar ID
       for the new Radar filled out in this column for the row. The tool writes out
       this copy after every row that produces a Radar.

    The second feature lets you recover from a failure halfway through the spreadsheet.
    When that happens, you can fix the issue in the input data or configuration and then
    re-run the tool, but this time you point it at the modified copy of the CSV file that
    the previous run created, instead of at the original CSV file.

    The tool writes the modified copy of the CSV into the same directory as the input
    CSV, under the original filename with a date/time suffix appended.

    - Example input filename: Radar_Creation_2019.csv
    - Example output filename: Radar_Creation_2019-updated-20191015-224042.csv

    Example::

        Radar ID:
            handler: RadarID

    """

    def should_skip_current_item(self):
        if bool(self.value()):
            return SkipRowDecision(True, 'Skipping because Radar ID value "{}" is present'.format(self.value()))
        return super(RadarID, self).should_skip_current_item()
    
    def preflight_will_skip_for_row_value(self, value):
        return bool(value)

    def update_row_for_csv_export(self, row, row_number):
        row[self.column_index] = radarclient.compat.unicode_string_type(self.radar_id)
        return True

    def can_provide_radar_id_for_row(self):
        return True
    
    def radar_id_for_row(self, row):
        return row[self.column_index]


class Assignee(FieldHandler):
    """
    This lets you assign the Radar to a person other than the default screener.

    The accepted values are "Firstname Lastname" or an email address. If the
    first/last name cannot be resolved uniquely to a Radar user, then the tool will
    fail when updating the radar and you have to use the email address instead.
    You can mix and match the two.

    Example::

        Assignee:
            handler: Assignee
            emailInsteadAmbiguousName: True

    When reading radar information to a CSV (i.e. in the ReadRadar subcommand mode),
    the tool can optioally identify ambiguous names and write out the email address
    instead of the name. You can enable this behavior with the "emailInsteadAmbiguousName"
    configuration file boolean option.
    
    """
    def parse_configuration(self):
        self.emailInsteadAmbiguousName = self.configuration.get('emailInsteadAmbiguousName', False)

    def preflight_start(self):
        self.assignees = {}

    def preflight_row_value(self, value, row_number, will_skip):
        if not value or value in self.assignees:
            return
        people = self.find_people(value)
        logging.debug('Resolved assignee value "{}" to: {}'.format(value, people))
        if len(people) > 1:
            raise Exception('Ambiguous assignee value "{}" in row {}: {}'.format(value, row_number, people))
        elif not people:
            raise Exception('Unable to resolve assignee value "{}" in row {}'.format(value, row_number))

        self.assignees[value] = people[0]
    
    def assignee_dsid(self):
        if self.value():
            person = self.assignees[self.value()]
            return person.dsid

    def update_row_for_csv_export(self, row, row_number):
        name = self.radar.assignee.firstName + ' ' + self.radar.assignee.lastName
        if self.emailInsteadAmbiguousName:
            people = self.find_people(name)
            if  len(people) > 1:
                logging.debug('Ambiguous assignee name: "{}" Providing email instead: {} '.format(name, self.radar.assignee.email.split("@")[0]))
                row[self.column_index] = self.radar.assignee.email.split("@")[0]
                return True
        row[self.column_index] = name
        return True

    def affected_radar_property_names(self):
        return {'assignee'}

    def get_property_data(self, property):
        property_data = None
        value = self.value()
        if value:
            person = self.assignees[value]
            property_data = Person({'dsid': person.dsid})
        return property_data

    def is_radar_data_identical(self, property, property_data):
        return False
        
    def find_people(self, value):
        people = None
        if ' ' not in value:
            email = value
            if '@' not in email:
                email = email + '@apple.com'
            people = self.context.radar_client.find_people(email=email)
        else:
            first, last = [], value.split()
            while len(last) > 1 and not people:
                first.append(last.pop(0))
                people = self.context.radar_client.find_people(firstName=' '.join(first), lastName=' '.join(last))
                if  len(people) > 1:
                    logging.debug('"find_people" returned several persons: {} with first name: {} and last name: {} '.format(people, ' '.join(first), ' '.join(last)))
                    people = self.people_exact_match(' '.join(first), ' '.join(last), people)
        return people

    def people_exact_match(self, first_name, last_name, found_people):
        exact_matches = [p for p in found_people if p.firstName == first_name and p.lastName == last_name]
        if len(exact_matches) == 1:
            return exact_matches
        return found_people

class Subtask(FieldHandler):
    """
    This handler lets you create multi-level subtask relationships between the
    created Radars.

    The value in this column must be some string (which you get to choose) that
    tells the tool what the hierarchy level of the row is. A common example is
    "TLF" and "Sub-TLF". In the configuration file, you have to assign a numeric
    hierarchy rank to each string that you intend to use. See example below.
    In this example the "TLF" is a higher (parent) level and it gets a lower number
    (1), and "Sub-TLF" is a lower (child) level and it gets a higher number (2).

    As the tool processes a row and finds a value in this column that assigns the
    row to a given hierarchy level, it will look backwards in the table until it
    finds an earlier row whose hierarchy level indicates that it should be the
    parent. The tool then creates the subtask relationship between the two.

    Here's a concrete example with some input data:

    ===========  ========  =======  =====  ===========
    Row Number   Radar ID  Type     Title  Description
    ===========  ========  =======  =====  ===========
              2            TLF      Foo    Bar  
              3            Sub-TLF  Foo    Bar  
              4            Sub-TLF  Foo    Bar  
              5            TLF      Foo    Bar  
              6            Sub-TLF  Foo    Bar  
              7            Sub-TLF  Foo    Bar  
              8            Feature  Foo    Bar
    ===========  ========  =======  =====  ===========

    Based on this data and the configuration example shown below, the following
    subtask relationships would be established:

    - Row 3 subtask of 2
    - Row 4 subtask of 2
    - Row 6 subtask of 5
    - Row 7 subtask of 5
    - Row 8 subtask of 5

    Example::

        Type:
            handler: Subtask
            level_mapping:
                - match: TLF
                  level: 1
                - match: Sub-TLF
                  level: 2
                - match: Feature
                  level: 2
    """

    def parse_configuration(self):
        if 'level_mapping' not in self.configuration:
            raise Exception('Subtask field handler requires a level_mapping configuration entry')

        self.level_map = {}
        for item in self.configuration['level_mapping']:
            name = item['match']
            if name in self.level_map:
                raise Exception('Duplicate level name "{}"'.format(name))
            self.level_map[name] = int(item['level'])
        configured_levels = sorted(set(self.level_map.values()))
        if configured_levels != list(range(1, len(configured_levels) + 1)):
            raise Exception('Inconsistent level numbering, check configuration for "{}": "{}"'.format(self.field_name, configured_levels))
        self.max_level = configured_levels[-1]

    def preflight_start(self):
        self.last_seen_row_for_level = [None] * (len(self.level_map.values()) + 1)
        self.parent_mapping = {}

    def preflight_row_value(self, value, row_number, will_skip):
        if not value:
            return

        if value not in self.level_map:
            raise Exception('Unknown level name "{}" in column "{}" in row {}'.format(value, self.field_name, row_number))

        level = self.level_map[value]
        self.last_seen_row_for_level[level] = row_number
        if level > 1:
            parent_row_number = self.last_seen_row_for_level[level - 1]
            if not parent_row_number:
                raise Exception('Inconsistent level numbering for column "{}" on row {}, row is level {} but there is no previous row with level {}'.format(self.field_name, row_number, level, level - 1))            
            self.parent_mapping[row_number] = parent_row_number
            logging.debug('Row {} is a subtask of row {}'.format(row_number, parent_row_number))

        if level < self.max_level:
            for i in range(level + 1, self.max_level + 1):
                self.last_seen_row_for_level[i] = None

    def update_current_radar(self, update_identical_values=False):
        if self.row_number not in self.parent_mapping:
            return
        parent_row_number = self.parent_mapping[self.row_number]
        parent_radar_id = self.context.radar_id_for_row_number(parent_row_number)

        if self.context.dry_run:
            print('dry run: Would set radar {} (row {}) as subtask of {} (row {})'.format(self.radar_id, self.row_number, parent_radar_id, parent_row_number))
            return

        logging.debug('Setting radar {} (row {}) as subtask of {} (row {})'.format(self.radar_id, self.row_number, parent_radar_id, parent_row_number))
        parent_radar = self.context.radar_client.radar_for_id(parent_radar_id)
        relationship = Relationship(Relationship.TYPE_SUBTASK_OF, self.radar, parent_radar)
        self.radar.add_relationship(relationship)

class RelatedToRadarID(FieldHandler):
    """
    This handler lets you create 'related to' relationships to radars that
    already exist before you run the tool.

    The input value is a string that contains the list of Radar IDs, separated by a comma.

    Example::

        Related Radar IDs:
            handler: RelatedToRadarID

    """

    def preflight_start(self):
        self.related_mapping = {}

    def preflight_row_value(self, value, row_number, will_skip):
        if not value:
            return
        radar_ids = value.split(",")

        for radar_id in radar_ids:
            related_radar_ids = re.findall(r'(\d+)', radar_id)
            if len(related_radar_ids) != 1:
                raise Exception('Unable to find Radar ID in string "{}" in column "{}" on row {}'.format(value, self.field_name, row_number))
            related_radar_id = related_radar_ids[0]

            if related_radar_id not in self.related_mapping:
                related_radar = self.context.radar_client.radar_for_id(related_radar_id)

                if not related_radar:
                    raise Exception('Unable to load related radar for id {} in column "{}" on row {}'.format(related_radar_id, self.field_name, row_number))

                self.related_mapping[related_radar_id] = related_radar

    def update_current_radar(self, update_identical_values=False):
        value = self.value()
        if not value:
            return
        radar_ids = value.split(",")

        for radar_id in radar_ids:
            radar_id = radar_id.strip()
            related_radar = self.related_mapping[radar_id]
            is_radar_data_identical = self.is_radar_data_identical(Relationship.TYPE_RELATED_TO, related_radar)
            if self.context.dry_run:
                if update_identical_values or not is_radar_data_identical:
                    print('dry run: Would set radar {} (row {}) related to {}'.format(self.radar_id, self.row_number, related_radar))
                continue

            if update_identical_values or not is_radar_data_identical:
                logging.debug('Setting radar {} (row {}) related to {}'.format(self.radar_id, self.row_number, related_radar))
                relationship = Relationship(Relationship.TYPE_RELATED_TO, self.radar, related_radar)
                self.radar.add_relationship(relationship)

    def is_radar_data_identical(self, relationship_type, target_radar):
        related_radars = None
        if self.radar:
            related_radars = self.radar.related_radars([relationship_type])
        if related_radars and target_radar:
            for related_radar in related_radars:
                if (radarclient.compat.unicode_string_type(related_radar.id) == radarclient.compat.unicode_string_type(target_radar.id)):
                    return True
        return False

    def update_row_for_csv_export(self, row, row_number):
        related_ids_list = []
        for related in self.radar.related_radars([Relationship.TYPE_RELATED_TO]):
            related_ids_list.append(radarclient.compat.unicode_string_type(related.id))
        row[self.column_index] = ', '.join(related_ids_list)
        return True

class SubtaskOfRadarID(FieldHandler):
    """
    This handler lets you create subtask relationships if the parent Radars
    already exist before you run the tool and you already know the parent
    Radar IDs (If you don't, see :py:class:`Subtask` for another way to
    create subtask relationships).

    The input value is a string that contains the parent Radar ID. It can be the
    bare Radar ID or a Radar URL or any other string that contains a single Radar
    ID.

    Example::

        Parent ID:
            handler: SubtaskOfRadarID

    """

    def preflight_start(self):
        self.parent_mapping = {}

    def preflight_row_value(self, value, row_number, will_skip):
        if not value or value in self.parent_mapping:
            return
        radars = value.split(",")
        for radar in radars:
            parent_radar_id = re.findall(r'(\d+)', radar.strip())
            if len(parent_radar_id) != 1:
                raise Exception('Unable to find Radar ID in string "{}" in column "{}" on row {}'.format(value, self.field_name, row_number))
            parent_radar_id = parent_radar_id[0]
            parent_radar = self.context.radar_client.radar_for_id(parent_radar_id)

            if not parent_radar:
                raise Exception('Unable to load parent radar for id {} in column "{}" on row {}'.format(parent_radar_id, self.field_name, row_number))
        
            self.parent_mapping[radar] = parent_radar
        
    def update_current_radar(self, update_identical_values=False):
        value = self.value()
        if not value:
            return
        radars = value.split(",")

        for radar in radars:
            parent_radar = self.parent_mapping[radar]
            is_radar_data_identical = self.is_radar_data_identical(Relationship.TYPE_SUBTASK_OF, parent_radar)
            if self.context.dry_run:
                if update_identical_values or not is_radar_data_identical:
                    print('dry run: Would set radar {} (row {}) as subtask of {}'.format(self.radar_id, self.row_number, parent_radar))
                continue
            
            if update_identical_values or not is_radar_data_identical:
                logging.debug('Setting radar {} (row {}) as subtask of {}'.format(self.radar_id, self.row_number, parent_radar))
                relationship = Relationship(Relationship.TYPE_SUBTASK_OF, self.radar, parent_radar)
                self.radar.add_relationship(relationship)

    def is_radar_data_identical(self, property, property_data):
        radar_data = None
        if self.radar:
            radar_data = self.radar.related_radars([property])
        if radar_data and property_data:
            for related in radar_data:
                if (radarclient.compat.unicode_string_type(related.id) == radarclient.compat.unicode_string_type(property_data.id)): return True
        return False

    def update_row_for_csv_export(self, row, row_number):
        parent_ids_list = []
        for related in self.radar.related_radars([Relationship.TYPE_SUBTASK_OF]):
            parent_ids_list.append(radarclient.compat.unicode_string_type(related.id))
        row[self.column_index] = ', '.join(parent_ids_list)
        return True

class BlockedBy(FieldHandler):
    """
    This handler lets you mark a Radar created for a row as being blocked by
    the Radar represented by another row.

    The value in the column for this handler represents a comma-separated list of
    references to other rows in the input spreadsheet. The tool does not use row
    numbers or Radar IDs for this purpose for the following reasons:

    - Row numbers are not very stable. If you re-run the tool after adding more rows,
      the references would be broken immediately.
    - We cannot use the Radar IDs because the blocking Radar might get created
      in the same run as the blocked Radar.
    
    For this reason, this handler requires that you add another column to the
    spreadsheet whose sole purpose is to identify referenced rows. The tool
    does not care what values you put into that column, except that they must
    be unique. One convention that works well is `work-breakdown structure (WBS)`_
    numbering, e.g. "1.2.1.4". It's easy to introduce additional values of this type
    that make sense if you have to add rows in the middle of an existing set of rows.

    You have to tell the tool the name of that other referenced column in the
    configuration file with the ``reference_column_name`` item as shown below.

    The values in this column (the referencing column) are then simply
    comma-separated lists of values that occur for other rows in that reference column.

    Here's a concrete example with some input data:

    ===========  ========  ==========  =======================  =====  ===========
     Row Number  Radar ID  WBS Number  Prerequisite WBS Number  Title  Description
    ===========  ========  ==========  =======================  =====  ===========
     2                            1.1                           Foo    Bar  
     3                            1.2  1.1                      Foo    Bar
     4                                 1.1, 1.2                 Foo    Bar
    ===========  ========  ==========  =======================  =====  ===========

    Based on this data and the configuration example shown below, the following
    blocking relationships would be established:

    - Row 3 blocked by 2
    - Row 4 blocked by 2
    - Row 4 blocked by 3

    Example::

        Prerequisite WBS Number:
            handler: BlockedBy
            reference_column_name: WBS Number

    .. _`work-breakdown structure (WBS)`: https://en.wikipedia.org/wiki/Work_breakdown_structure#Coding_scheme

    """
    def parse_configuration(self):
        key = 'reference_column_name'
        reference_column_name = self.configuration.get(key, None)
        if not reference_column_name:
            raise Exception('BlockedBy field handler requires a {} configuration entry'.format(key))
        if not self.context.is_known_field_name(reference_column_name):
            raise Exception('BlockedBy configuration key {} for column "{}" refers to unkown column name "{}"'.format(key, self.field_name, reference_column_name))
        self.reference_column_name = reference_column_name

    def preflight_start(self):
        self.reference_value_to_row_number_map = {}
        self.blocked_by_map = collections.defaultdict(list)
        self.unresolved_references_map = collections.defaultdict(list)

    def preflight_row_value(self, value, row_number, will_skip):
        reference_column_value = self.context.value_for_field_and_row_number(self.reference_column_name, row_number)
        self.preflight_row_process_reference_value(reference_column_value, row_number)

        if not value:
            return

        values = split_comma(value)
        for value in values:
            if value == reference_column_value:
                raise Exception('BlockedBy field handler for column "{}" encountered row that references its own value "{}" in column "{}" on row {}'.format(self.field_name, value, self.reference_column_name, row_number))
            target_row_number = self.reference_value_to_row_number_map.get(value, None)
            if target_row_number:
                self.blocked_by_map[row_number].append(target_row_number)
            else:
                # We have not yet seen a row that has the reference value referenced by this row.
                # This could either be an invalid value, or a value on a row not yet processed.
                # Create a fix-up task for this reference value to be resolved later.
                logging.debug('BlockedBy field handler for column "{}" encountered unknown reference value "{}" (in column "{}") on row {}, noting as possible forward reference for future resolution'.format(self.field_name, value, self.reference_column_name, row_number))
                self.unresolved_references_map[value].append(self.fixup_task_for_row_number(row_number, value))

    def preflight_row_process_reference_value(self, reference_column_value, row_number):
        if not reference_column_value:
            return
        if reference_column_value in self.reference_value_to_row_number_map:
            raise Exception('BlockedBy field handler for column "{}" encountered duplicate reference value "{}" in column "{}" on row {} (previous occurrence on row {})'.format(self.field_name, reference_column_value, self.reference_column_name, row_number, self.reference_value_to_row_number_map[reference_column_value]))
        self.reference_value_to_row_number_map[reference_column_value] = row_number
        forward_reference_fixup_task_list = self.unresolved_references_map.get(reference_column_value, None)
        if forward_reference_fixup_task_list:
            # This row was referenced from one or more earlier ones and we stored one or more
            # fix-up tasks. Run them now to resolve the forward reference to the current row.
            blocked_row_numbers = [task.row_number for task in forward_reference_fixup_task_list]
            logging.debug('BlockedBy field handler for column "{}" found {} forward reference fixup tasks for reference value "{}" on row {}, resolving reference(s) on row(s) {}'.format(self.field_name, len(forward_reference_fixup_task_list), reference_column_value, row_number, blocked_row_numbers))
            del(self.unresolved_references_map[reference_column_value])
            for task in forward_reference_fixup_task_list:
                task.fixup_handler(row_number)

    def preflight_end(self):
        if self.unresolved_references_map:
            # After processing all rows, there are fixup tasks left in the unresolved_references_map, which means
            # that one of the rows referenced a value that does not occur in the reference column at all.
            errors = []
            for reference_value, fixup_task_list in self.unresolved_references_map.items():
                errors.append('Reference value "{}" referenced on rows {}'.format(reference_value, [task.row_number for task in fixup_task_list]))
            raise Exception('BlockedBy field handler for column "{}" encountered unresolved references: {}'.format(self.field_name, ', '.join(errors)))

    def fixup_task_for_row_number(self, row_number, reference_value):
        def fixup_handler(target_row_number):
            self.blocked_by_map[row_number].append(target_row_number)
            logging.debug('BlockedBy field handler for column "{}" running fixup handler for forward reference value "{}" referenced on row {}, resolved to row {}'.format(self.field_name, reference_value, row_number, target_row_number))
        return FixupTask(row_number, fixup_handler)

    def update_current_radar(self, update_identical_values=False):
        blocked_by_row_numbers = self.blocked_by_map.get(self.row_number, None)
        if blocked_by_row_numbers:
            self.create_blocked_by_relationship(blocked_by_row_numbers, self.radar, self.radar_id, self.row_number, self.context)

    def update_row_for_csv_export(self, row, row_number):
        blockedby_ids_list = []
        for related in self.radar.related_radars([Relationship.TYPE_TYPE_BLOCKED_BY]):
            blockedby_ids_list.append(related.id)
        row[self.column_index] = ', '.join(blockedby_ids_list)
        return True
            
    @classmethod
    def create_blocked_by_relationship(cls, blocked_by_row_numbers, blocked_radar, blocked_radar_id, blocked_radar_row_number, context):
        for blocked_by_row_number in blocked_by_row_numbers:
            blocked_by_radar_id = context.radar_id_for_row_number(blocked_by_row_number)
            if context.dry_run:
                print('dry run: Would set radar {} (row {}) as blocked by {} (row {})'.format(blocked_radar_id, blocked_radar_row_number, blocked_by_radar_id, blocked_by_row_number))
                continue
            logging.debug('Setting radar {} (row {}) as blocked by {} (row {})'.format(blocked_radar_id, blocked_radar_row_number, blocked_by_radar_id, blocked_by_row_number))
            blocked_by_radar = context.radar_client.radar_for_id(blocked_by_radar_id)
            relationship = Relationship(Relationship.TYPE_BLOCKED_BY, blocked_radar, blocked_by_radar)
            blocked_radar.add_relationship(relationship)


class OtherRelatedItems(FieldHandler):
    """
    This handler lets you create "Other Related Item" entries for a Radar.

    The value in this colum becomes the "ID" of the entry. 

    You must have previously created the Radar system favorite in the
    Radar preferences. You then have to tell the tool the name
    of that favorite in the configuration file as shown below.

    Example::

        DefectID:
            handler: OtherRelatedItems
            system_name: 'PhotoApps Crucible'
    """

    def update_current_radar(self, update_identical_values=False):
        item_data = {'id': self.value(), 'system': self.configuration['system_name'], 'title': self.value()}
        if self.context.dry_run:
            print('dry run: would create other related item entry: {}'.format(item_data))
            return

        item = OtherRelatedItem(item_data)
        self.radar.other_related_items.add(item)


class SimpleProperty(FieldHandler):
    """
    This handler directly copies the value from the spreadsheet cell into a property
    of the new Radar. You choose which property with the ``property`` parameter.

    Example::

        Title:
            handler: SimpleProperty
            property: title

    You can optionally skip rows if the value (converted to a string) matches a
    regular expression that you pass with the ``skip_value_regex`` option.

    In this example, titles that contain a Radar URL are skipped::

        Title:
            handler: SimpleProperty
            property: title
            skip_value_regex: rdar://\\d+

    """

    def parse_configuration(self):
        self.property_name = self.validated_property_name_from_configuration()
        self.can_apply_pre_create = self.property_name in 'title component originator description diagnosis classification reproducible fixOrder taskOrder configuration effortCurrentTotalEstimate'.split()
        if self.property_name in self.known_integer_properties() and not self.has_value_transformer_of_type(ConvertToInteger):
            self.add_value_transformer(ConvertToInteger())

        self.skip_value_regex = self.validated_skip_regex_from_configuration()

    @staticmethod
    def known_integer_properties():
        return set(['assigneeID', 'driID', 'duplicateOfProblemID', 'fixOrder', 'labelID', 'originatorID', 'priority', 'resolverID', 'effortCurrentTotalEstimate'])

    def validated_property_name_from_configuration(self):
        value = self.configuration['property']
        if re.match(r'[A-Z]', value):
            raise Exception('Property name in SimpleProperty field handler configuration looks invalid, must start with a lowercase letter: "{}"'.format(value))
        if not re.match(r'[a-z][a-zA-Z0-9]+$', value):
            raise Exception('Property name in SimpleProperty field handler configuration looks invalid: "{}"'.format(value))
        return value

    def validated_skip_regex_from_configuration(self):
        value = self.configuration.get('skip_value_regex')
        if not value:
            return None

        try:
            return re.compile(value)
        except re.error as e:
            raise Exception('Invalid skip_value_regex regular expression "{}" in configuration for SimpleProperty "{}": "{}"'.format(value, self.property_name, e))

    def should_skip_current_item(self):
        if self.skip_value_regex and self.skip_value_regex.search(str(self.value())):
            return SkipRowDecision(True, 'Skipping because value "{}" matches skip regex'.format(self.value()))
        return super(SimpleProperty, self).should_skip_current_item()

    def update_new_radar_data(self, new_radar_data):
        if self.can_apply_pre_create:
            self.skip_update_current_radar = True
            value = self.value()
            if value != '':
                new_radar_data[self.property_name] = value
            else:
                logging.debug('Not setting radar property "{}" for row {} because value is empty'.format(self.property_name, self.row_number))
        
    def preflight_row_value(self, value, row_number, will_skip):
        if will_skip:
            return
        if self.property_name in ['title', 'description', 'classification']:
            if not value:
                raise Exception('Missing value in column "{}" on row {} for mandatory property "{}"'.format(self.field_name, row_number, self.property_name))

    def update_row_for_csv_export(self, row, row_number):
        update_csv = False
        radar_info = getattr(self.radar, self.property_name)
        if (radar_info) is not None:
            self.set_current_row(row, radar_info, row_number)
            row[self.column_index] = self.value()
            update_csv = True
        else:
            row[self.column_index] = ""
        return update_csv
            
    def affected_radar_property_names(self):
        return {self.property_name}


class AttachFile(FieldHandler):
    """
    This handler interprets the spreadsheet cell value as a list of paths
    (absolute or relative) to files. It attaches those files to the new Radar.
    Multiple paths must be separated by commas or newlines.

    Example::

        Attachment:
            handler: AttachFile
        
    """

    def preflight_row_value(self, value, row_number, will_skip):
        if will_skip:
            return

        file_paths = self.file_paths_for_raw_value(value)
        for path in file_paths:
            if not (os.path.exists(path) and os.path.isfile(path)):
                raise Exception('Attachment file path in column "{}" on row {} does not exist or is not a file: {}'.format(self.field_name, row_number, path))
        
    def update_current_radar(self, update_identical_values=False):
        file_paths = self.file_paths_for_raw_value(self.value())
        if self.context.dry_run:
            print('dry run: would attach files "{}"'.format(', '.join(file_paths)))
            return

        for path in file_paths:
            _, filename = os.path.split(path)
            attachment = self.radar.new_attachment(filename)
            attachment.set_upload_file(open(path, 'rb'))
            self.radar.attachments.add(attachment)

    @staticmethod
    def file_paths_for_raw_value(value):
        return filter(None, re.split(r'\s*[\n,]\s*', value.strip(), flags=re.MULTILINE))



class State(FieldHandler):
    """
    This handler matches the column's value against a series of regular expressions.
    The first one that matches defines an action for the row, and there are currently
    two actions:

    - Assign a Radar state (Analyze etc.) to the Radar created for the row
    - Skip this row without creating a Radar

    You pick one of those actions by assigning the appropriate "state handler"
    and configuring it as shown below. There are two state handlers corresponding
    to the two actions described above, ``SimpleState`` and ``SkipItem``.

    This handler can be useful if you are migrating/importing bugs from another bug
    tracking system and you want to skip rows whose state in the other system
    indicates that they are not interesting and don't need to be migrated
    to Radar.
    
    Example::

        State:
            handler: State
            state_mapping:
                - match: New
                  handler: SimpleState
                  state: Analyze
                  substate: Screen
                - match: Assigned
                  handler: SimpleState
                  state: Analyze
                  substate: Investigate
                  priority: 2
                - match: .*
                  handler: SkipItem
    """

    def update_for_current_row(self):
        for mapping in self.configuration['state_mapping']:
            if re.match(mapping['match'], self.value()):
                self.state_handler = create_handler(mapping, StateHandler)
                return
        raise Exception('No state mapping match for input state {}'.format(self.value()))
        
    def should_skip_current_item(self):
        return self.state_handler.should_skip_current_item()
    
    def update_current_radar(self, update_identical_values=False):
        self.state_handler.update_radar(self.radar)

    def affected_radar_property_names(self):
        return {'state', 'substate', 'priority'}
    

class StateHandler(object):
    
    def __init__(self, configuration):
        self.configuration = configuration
    
    def set_current_value(self, value):
        self.value = value
    
    def should_skip_current_item(self):
        return SkipRowDecision(False, None)

    def update_radar(self, radar):
        raise Exception('Subclass {} must override this method'.format(type(self).__name__))


class SimpleState(StateHandler):

    def update_radar(self, radar):
        if not radar:
            print('dry run: would update radar to state/substate {}/{}'.format(self.configuration['state'], self.configuration.get('substate', None)))
            return 

        radar.state = self.configuration['state']
        if self.configuration['state'] == 'Analyze' and self.configuration['substate']:
            radar.substate = self.configuration['substate']
            priority = self.configuration.get('priority', None)
            if priority:
                radar.priority = priority


class SkipItem(StateHandler):
    
    def should_skip_current_item(self):
        return SkipRowDecision(True, 'Skipping based on Radar state mapping configuration')
    

"""
    Abstract subcommand class contains common functionality, which other subcommands can inherit (and reuse)

"""
class AbstractExcelSubcommand(AbstractSubcommand):

    def __init__(self, *args, **kwargs):
        super(AbstractExcelSubcommand, self).__init__(*args, **kwargs)
        self._configuration = None

    def __call__(self):
        self.load_document()
        self.create_field_handlers()
        self.check_configuration_and_input_file_consistency()
        self.setup_updated_csv_path()
        
    def configuration(self):
        if not self._configuration:
            path = self.args.configuration_file
            with open(path) as f:
                self._configuration = yaml.safe_load(f)
        return self._configuration
        
    def load_document(self):
        self.header_row = None
        self.data_rows = []

        path = self.args.csv_file
        with open(path, mode='rb') as f:
            self.input_file_has_utf8_byte_order_mark = f.read(3) == b'\xef\xbb\xbf'

        with io.open(path, radarclient.compat.open_flags_read_unified_line_endings, encoding='utf-8-sig') as f:
            reader = unicode_csv_reader(f, delimiter=(radarclient.compat.file_output_representation_for_unicode_string(self.args.csv_delimiter)))
            self.header_row = next(reader, None)
            if not self.header_row:
                raise Exception('Unable to find column names in first row')
            self.data_rows = list(reader)

        self.field_map = {name.strip(): index for index, name in enumerate(self.header_row) if bool(name.strip())}

        ncols = len(self.header_row)
        nrows = len(self.data_rows)
        logging.debug('Loaded {} data rows with {} columns, fieldnames: {}'.format(nrows, ncols, self.field_map))
        if nrows < 1:
            raise Exception('File must contain at least one non-header row')

    def create_field_handlers(self):
        dry_run = self.args.dry_run if hasattr(self.args, 'dry_run') else False       
        context = FieldHandlerContext(datasource=self, field_map=self.field_map, radar_client=self.radar_client, dry_run=dry_run)
        handlers = {}
        for field_name, configuration in self.configuration()['field_mapping'].items():
            column_index = self.field_map.get(field_name, None)
            if column_index is not None:
                handlers[field_name] = create_handler(configuration, FieldHandler, field_name, column_index, context)
        self.field_handlers = handlers
 
    def check_configuration_and_input_file_consistency(self):
        configuration_field_names = set(self.configuration()['field_mapping'].keys())
        data_field_names = set(self.field_map.keys())
        missing_field_names = configuration_field_names - data_field_names
        ignored_field_names = data_field_names - configuration_field_names

        logging.debug('Data field names: {}, configuration field names: {}, missing from data: {}, ignored from data: {}'.format(data_field_names, configuration_field_names, missing_field_names, ignored_field_names))

        if missing_field_names:
            if self.args.ignore_missing_fields:
                self.print_flush('Ignoring columns present in configuration file but not in input file: {}'.format(', '.join(missing_field_names)))
            else:
                raise Exception(radarclient.compat.file_output_representation_for_unicode_string('Field names present in configuration but missing in input file: {}'.format(', '.join(missing_field_names))))

        if ignored_field_names:
            self.print_flush('Ignoring columns present in input but not in configuration file: {}'.format(', '.join(ignored_field_names)))

        unique_names = set()
        for name in [x for x in self.header_row if x]:
            if name in unique_names:
                raise Exception('Duplicate input data column name "{}"'.format(name))
            unique_names.add(name)
            
        start_row_min = 2
        start_row_max = len(self.data_rows) + 1
        if self.args.start_row < start_row_min or self.args.start_row > start_row_max:
            raise Exception('Valid start rows are from {} to {}'.format(start_row_min, start_row_max))

    def skip_row(self, row, row_number, total_rows_processed):
        if self.is_blank_row(row):
            logging.debug('Skipping input row number {} because it is blank'.format(row_number))
            return True
        if row_number < self.args.start_row:
            logging.debug('Skipping input row number {} because it is lower than start row number {}'.format(row_number, self.args.start_row))
            return True
        if self.args.row_limit and total_rows_processed >= self.args.row_limit:
            logging.debug('Stopping at input row number {} because processed row limit of {} is reached'.format(row_number, self.args.row_limit))
            return True

    def is_blank_row(self, row):
        non_blank_cells = [x for x in self.field_handlers.keys() if self.row_value(row, x)]
        return not non_blank_cells

    def row_value(self, row, field_name):
        return row[self.field_map[field_name]].strip()

    def row_for_row_number(self, row_number):
        return self.data_rows[row_number - 2]
        
    def radar_property_names(self):
        property_names = set()
        for handler in self.field_handlers.values():
            property_names |= handler.affected_radar_property_names()
        return property_names
        
    def setup_updated_csv_path(self):
        dirname, basename = os.path.split(self.args.csv_file)
        basename = re.sub(r'-updated-........-......', '', basename)
        head, ext = os.path.splitext(basename)
        export_filename = '{}-updated-{}{}'.format(head, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'), ext)
        self.updated_csv_path = os.path.join(dirname, export_filename)
        logging.debug('Updated output CSV path: {}'.format(self.updated_csv_path))
 
    def save_data_to_csv(self):
        logging.debug('Writing updated CSV file to "{}" (UTF-8 byte order mark: {})'.format(self.updated_csv_path, self.input_file_has_utf8_byte_order_mark))
        with open(self.updated_csv_path, 'w') as f:
            if self.input_file_has_utf8_byte_order_mark:
                f.write(radarclient.compat.file_output_representation_for_unicode_string(u'\ufeff'))
            writer = csv.writer(f)
            writer.writerow(unicode_encode_row(self.header_row))
            for line_number, row in enumerate(self.data_rows, start=2):
                writer.writerow(unicode_encode_row(row))

    def check_exception(self, e):
        if ("Non-200 response code 500: Internal Server Error" in radarclient.compat.unicode_string_type(e)): return True;
        if ("Non-200 response code 502: Bad Gateway" in radarclient.compat.unicode_string_type(e)): return True;
        if ("Non-200 response code 503: Service Unavailable" in radarclient.compat.unicode_string_type(e)): return True
        if ("Non-200 response code 504: Gateway Time-out" in radarclient.compat.unicode_string_type(e)): return True;
        if ("The read operation timed out" in radarclient.compat.unicode_string_type(e)): return True;
        if ("Non-200 response code 404: Not Found" in radarclient.compat.unicode_string_type(e)): return True;
        return False

    def print_flush(self, argument):
        print(argument)
        sys.stdout.flush()
        
    @classmethod
    def configure_argument_parser(cls, parser):
        parser.add_argument('configuration_file', help='The YAML configuration file')
        parser.add_argument('csv_file', help='The input Excel file')
        parser.add_argument('--dry-run', action='store_true', help='Test mode / dry run')
        parser.add_argument('--start-row', type=int, default=2, help='Optional start row index, in case a previous run was aborted. This is a row number as it appears in the spreadsheet application, i.e. row index 2 is the first data row (after the header row at index 1). So for an input table with two data rows, the valid values would be 2 and 3.')
        parser.add_argument('--row-limit', type=int, default=None, help='Optional row limit, to process a subset of the rows in the input file')
        parser.add_argument('--csv-delimiter', default=",", help='Optional delimiter setting for the csv input file')
        parser.add_argument('--ignore-missing-fields', action='store_true', help='Ignore fields that are defined in the YAML configuration file but missing in the input CSV file')


"""
    Subcommand to print the configuration file (*.yml)
    It inherits from the common subcommand functionality class (AbstractExcelSubcommand)
    
"""
class SubcommandDumpConfig(AbstractExcelSubcommand):

    def __call__(self):
        pprint.pprint(self.configuration())


"""
    Subcommand to read radar information from the radar database and save the output into csv file
    The user specifies the radar ids to be read (in *.csv file) or the numeric query ID defined in the radar database
    User can configure the appropriate handlers to be used for handling each radar field (e.g. title, component, state etc.)
    The class inherits from the common subcommand functionality class (AbstractExcelSubcommand)

    Note that there is a Radar server side bug, sometimes triggering timeout errors. A retry workaround was implemented
    (up to 3 trials before failing). See <rdar://problem/98038297> for latest server-side status
    
"""
class SubcommandReadRadars(AbstractExcelSubcommand):

    def __call__(self):
        super(SubcommandReadRadars, self).__call__()
        self.read_radars()
        
    def read_radars(self):
        csv_radar_ids = self.read_csv_radars()
        if self.args.radar_query:
            self.read_query_radars(csv_radar_ids)
        self.save_data_to_csv()
        
    def read_csv_radars(self):
        csv_radar_ids = []
        row_count = 0
        for current_row_number, row in enumerate(self.data_rows, start=2):
            radar_id = self.get_radar_id_for_row_number(current_row_number)
            if radar_id.isnumeric():
                csv_radar_ids.append(int(radar_id))
            if self.skip_row(row, current_row_number, row_count):
                self.print_flush('Skipping Row {}: row empty or out of requested process range'.format(current_row_number))
                continue
            self.read_data_from_radar_to_row(radar_id, row, current_row_number)
            row_count += 1
        return csv_radar_ids

    def get_radar_id_for_row_number(self, row_number):
        for name, handler in self.field_handlers.items():
            if not handler.can_provide_radar_id_for_row():
                continue
            row = self.row_for_row_number(row_number)
            return handler.radar_id_for_row(row)

    def read_query_radars(self, skip_radar_ids):
        query_radar_ids = self.query_radar()
        logging.debug('query radars ids: : {}'.format(query_radar_ids))
        current_row_number = len(self.data_rows) + 2
        self.print_flush('Query processing... checking if should add rows to csv')
        for radar_id in query_radar_ids:
            if (radar_id not in skip_radar_ids):
                row = [""] * len(self.header_row)
                self.read_data_from_radar_to_row(radar_id, row, current_row_number)
                self.data_rows.append(row)
                current_row_number += 1
    
    def read_data_from_radar_to_row(self, radar_id, row, row_number):
        tries = 3
        for i in range(tries):
            try:
                if isinstance (radar_id, int) or isinstance (radar_id, radarclient.compat.unicode_string_type) and radar_id.isnumeric():
                    radar = self.radar_client.radar_for_id(radar_id, additional_fields=list(self.radar_property_names()))
                    logging.debug('*** Processing row {}: {}'.format(row_number, row))
                    for name, handler in self.field_handlers.items():
                        handler.set_current_radar_and_id(radar, radar.id)
                        handler.update_row_for_csv_export(row, row_number)
                    self.print_flush('Row {}: rdar://problem/{}'.format(row_number, radar.id))
                else:
                    self.print_flush('Skipping Row {}: radar id is not an integer figure: {}'.format(row_number, radar_id))
            except Exception as e:
                retry_flag = self.check_exception(e.args)
                if retry_flag and (i < tries - 1):
                    print('Retrying due to failure in row number {} while trying to read radar id {}: {}'.format(row_number, radar_id, e), file=sys.stderr)
                    continue
                else:
                    print('Error in row number {} while trying to read radar id {}: {}'.format(row_number, radar_id, e), file=sys.stderr)
                    raise
            break

    def query_radar(self):
        query_radar_ids = []
        for report_id in self.args.radar_query:
            radar_ids_list = self.radar_client.radar_ids_for_query(report_id, limit=self.args.limit)
            query_radar_ids.extend(radar_ids_list)
        return query_radar_ids

    @classmethod
    def configure_argument_parser(cls, parser):
        super(SubcommandReadRadars, cls).configure_argument_parser(parser)
        parser.add_argument('--radar-query', action='append', type=int, help='Find Radars by executing a query. Takes one argument, the numeric report ID. Can be passed multiple times.')
        parser.add_argument('--limit', type=int, help='Limit to a given number of results')


"""
    Subcommand for updating radar information in radar database
    The user specifies the radar ids to be updated along the radar fields inputs in *.csv file
    User can configure the appropriate handlers to be used for handling each radar field (e.g. title, component, state etc.)
    The class inherits from the common subcommand functionality class (AbstractExcelSubcommand)
    
    Note: There is a Radar server side bug, sometimes triggering timeout errors. A retry workaround was implemented
    (up to 3 trials before failing). See <rdar://problem/98038297> for latest server-side status """
class SubcommandUpdateRadars(AbstractExcelSubcommand):
    def __call__(self):
        super(SubcommandUpdateRadars, self).__call__()
        self.write_radars()
        

    def write_radars(self):
        radar_ids = [];
        row_count = 0
        radar_property_names = self.radar_property_names()
        logging.debug('Affected Radar properties: {}'.format(', '.join(radar_property_names)))
        
        for name, handler in self.field_handlers.items():
            handler.preflight_start()
        
        try:
            for current_row_number, row in enumerate(self.data_rows, start=2):
                if self.skip_row(row, current_row_number, row_count):
                    continue
                logging.debug('*** Processing row {}: {}'.format(current_row_number, row))

                radar_id = self.get_radar_id_for_row_number(current_row_number)
                self.update_radar(radar_id, row, current_row_number, radar_property_names)
                row_count += 1

        except Exception as e:
            print('Error in row number: {} (Radar {}): {}'.format(current_row_number, radar_id, e), file=sys.stderr)
            raise

    def update_radar(self, radar_id, row, row_number, radar_property_names):
        tries = 3
        forced_update = False
        for i in range(tries):
            try:
                if radar_id.isnumeric():
                    radar = self.radar_client.radar_for_id(radar_id, additional_fields=list(radar_property_names))
                    if radar:
                        for name, handler in self.field_handlers.items():
                            value = self.row_value(row, name)
                            handler.preflight_row_value(value, row_number, False)
                            handler.set_current_row(row, value, row_number)
                            handler.set_current_radar_and_id(radar, radar.id)
                            handler.update_current_radar(forced_update)
                        if self.args.dry_run:
                            print('dry run: would commit radar ({}) data'.format(radar_id))
                        else:
                            forced_update = True
                            radar.commit_changes()

                        self.print_flush('Row {}: rdar://problem/{}'.format(row_number, radar_id))
                    else:
                        self.print_flush('Skipping Row {}: radar id not found: {}'.format(row_number, radar_id))
                else:
                    self.print_flush('Skipping Row {}: radar id is not a numerical figure: {}'.format(row_number, radar_id))
            except Exception as e:
                retry_flag = self.check_exception(e.args)
                if retry_flag and (i < tries - 1):
                    print('Retrying due to failure in row number {} while was trying to read radar id {}: {}'.format(row_number, radar_id, e), file=sys.stderr)
                    continue
                else:
                    print('Error while trying to commit Radar changes for row {} ({}) (Radar {}): {}'.format(row_number, row, radar_id, e), file=sys.stderr)
                    raise
            break

    def get_radar_id_for_row_number(self, row_number):
        for name, handler in self.field_handlers.items():
            if not handler.can_provide_radar_id_for_row():
                continue
            row = self.row_for_row_number(row_number)
            return handler.radar_id_for_row(row)

    @classmethod
    def configure_argument_parser(cls, parser):
        super(SubcommandUpdateRadars, cls).configure_argument_parser(parser)

"""
    Subcommand for creation of (new) radars in radar database
    Created radar ids are saved in a csv file along the user inputs:
    - The user specifies in *.csv file the information for the radars to be created
    - The user can configure the appropriate handler to be used for each radar field (e.g. title, component, state etc.)
    The class inherits from the common subcommand functionality class (AbstractExcelSubcommand)
    
"""
class SubcommandCreateRadars(AbstractExcelSubcommand):

    def __call__(self):
        super(SubcommandCreateRadars, self).__call__()
        self.validate_configuration_defaults()
        self.validate_input_data()
        self.create_radars()
    
    @staticmethod
    def allowed_default_radar_values_properties():
        return ['classification', 'reproducible', 'componentID']

    def validate_configuration_defaults(self):
        defaults = self.configuration().get('default_radar_values', None)
        if not defaults:
            return
        
        if type(defaults) != dict:
            raise Exception('Type of "default_radar_values" key in configuration must be dict, not {}'.format(type(defaults)))
            
        allowed_keys = ['assignee_dsid'] + self.allowed_default_radar_values_properties()
        unknown_keys = [k for k in defaults.keys() if k not in allowed_keys]
        if unknown_keys:
            raise Exception('Invalid keys in "default_radar_values" dictionary in configuration: {}'.format(', '.join(unknown_keys)))
        
        keyword_id = self.configuration().get('new_radar_keyword_id', None)
        if keyword_id:
            self.keyword = self.radar_client.keywords_for_ids([keyword_id])[0]
        else:
            self.keyword = None
            
    def validate_input_data(self):
        for name, handler in self.field_handlers.items():
            handler.preflight_start()

        for row_number, row in enumerate(self.data_rows, start=2):
            if self.is_blank_row(row):
                continue
            will_skip = False
            for name, handler in self.field_handlers.items():
                value = self.row_value(row, name)
                will_skip = handler.preflight_will_skip_for_row_value(value)
                if will_skip:
                    break
            for name, handler in self.field_handlers.items():
                value = self.row_value(row, name)
                handler.preflight_row_value(value, row_number, will_skip)

        for name, handler in self.field_handlers.items():
            handler.preflight_end()

    def create_radars(self):
        start_row = self.args.start_row
        row_count = 0
        radar_property_names = self.radar_property_names()
        logging.debug('Affected Radar properties: {}'.format(', '.join(radar_property_names)))
        try:
            for current_row_number, row in enumerate(self.data_rows, start=2):
                if self.skip_row(row, current_row_number, row_count):
                    continue
                logging.debug('*** Processing row {}: {}'.format(current_row_number, row))
                self.current_row = row
                self.current_row_number = current_row_number
                self.create_radar_for_current_row(radar_property_names)
                row_count += 1
                
        except Exception as e:
            print('Error in row number: {} (create radar): {}'.format(current_row_number, e), file=sys.stderr)
            raise

    def create_radar_for_current_row(self, radar_property_names):
        pre_create_handlers = []
        post_create_handlers = []
        handlers = []

        row = self.current_row
        current_row_number = self.current_row_number

        for field_name, handler in self.field_handlers.items():
            value = self.row_value(row, field_name)
            handler.set_current_row(row, value, current_row_number)
            handlers.append(handler)

        decision = self.create_radar_check_should_skip(handlers)
        if decision.should_skip:
            return

        new_radar_data = self.create_radar_step1_prepare_data(handlers)
        radar, radar_id = self.create_radar_step2_perform_creation(new_radar_data, radar_property_names)
        self.create_radar_step3_set_radar_on_handlers(handlers, radar, radar_id)
        self.create_radar_step4_perform_post_creation_updates(handlers)
        self.create_radar_step5_set_assignee(handlers, radar, radar_id)
        self.create_radar_step6_assign_keyword(radar)
        self.create_radar_step7_export_updated_csv_file(handlers)

        if radar:
            try:
                radar.commit_changes()
            except Exception as e:
                print('Error while trying to commit Radar changes for row {} ({}) (Radar {}): {}'.format(current_row_number, row, radar_id, e), file=sys.stderr)
                raise

    def radar_property_names(self):
        property_names = set()
        for handler in self.field_handlers.values():
            property_names |= handler.affected_radar_property_names()
        return property_names

    def create_radar_check_should_skip(self, handlers):
        for handler in handlers:
            decision = handler.should_skip_current_item()
            if decision.should_skip:
                print('Handler {} for column "{}" skipped row {}: {}'.format(type(handler).__name__, handler.field_name, self.current_row_number, decision.reason))
                return decision
        return SkipRowDecision(False, None)

    def create_radar_step1_prepare_data(self, handlers):
        new_radar_data = {}
        for handler in handlers:
            handler.update_new_radar_data(new_radar_data)

        defaults = self.configuration().get('default_radar_values', None)
        if defaults:
            for key in self.allowed_default_radar_values_properties():
                value = defaults.get(key, 'None')
                if key not in new_radar_data and value:
                    new_radar_data[key] = value        

        if 'description' not in new_radar_data and self.configuration().get('replace_missing_description_with_title', False):
            if 'title' in new_radar_data:
                new_radar_data['description'] = new_radar_data['title']

        logging.debug('Prepared new radar data for row {}: {}'.format(self.current_row_number, new_radar_data))

        mandatory_keys = 'description', 'title', 'classification', 'componentID'
        missing_keys = [key for key in mandatory_keys if not new_radar_data.get(key, None)]
        if missing_keys:
            raise Exception('Missing initial Radar data items on row {}: {}'.format(self.current_row_number, ', '.join(missing_keys)))

        return new_radar_data
    
    def create_radar_step2_perform_creation(self, new_radar_data, radar_property_names):
        if self.args.dry_run:
            radar = None
            radar_id = 'dummy-radar-id-{}'.format(random.randint(1, 10000))
            print('dry run: would create radar with initial data ({}): {}'.format(radar_id, new_radar_data))
        else:
            radar = self.radar_client.create_radar(new_radar_data)
            radar_id = radar.id
            if radar.is_placeholder():
                raise Exception('Row {}: rdar://problem/{} was filed into a drop box to which you do not have access, please change the component configuration or use a different account.'.format(self.current_row_number, radar_id))
            else:
                # Reload to support non-standard attributes
                radar = self.radar_client.radar_for_id(radar_id, additional_fields=list(radar_property_names))
                print('Row {}: rdar://problem/{}'.format(self.current_row_number, radar_id))

        logging.debug('New Radar ID for row {}: {}'.format(self.current_row_number, radar_id))
        return radar, radar_id
    
    def create_radar_step3_set_radar_on_handlers(self, handlers, radar, radar_id):
        for handler in handlers:
            handler.set_current_radar_and_id(radar, radar_id)

    def create_radar_step4_perform_post_creation_updates(self, handlers):
        for handler in handlers:
            handler.update_current_radar()

    def create_radar_step5_set_assignee(self, handlers, radar, radar_id):
        assignee_dsid = self.assignee_dsid_from_handlers_or_default(handlers)
        if assignee_dsid:
            logging.debug('Initial assignee DSID {}'.format(assignee_dsid))
            if radar:
                assignee = Person({'dsid': assignee_dsid})
                radar.assignee = assignee
            else:
                print('dry run: would assign radar {} to DSID {}'.format(radar_id, assignee_dsid))

    def assignee_dsid_from_handlers_or_default(self, handlers):
        for handler in handlers:
            dsid = handler.assignee_dsid()
            if dsid:
                logging.debug('Handler {} provided initial assignee DSID {}'.format(handler, dsid))
                return dsid

        defaults = self.configuration().get('default_radar_values', None)
        if defaults:
            return defaults.get('assignee_dsid', None)

    def create_radar_step6_assign_keyword(self, radar):
        if self.keyword:
            if radar:
                radar.add_keyword(self.keyword)
            else:
                print('dry run: would assign keyword with ID {}'.format(self.keyword.id))
    
    def create_radar_step7_export_updated_csv_file(self, handlers):
        row = self.current_row
        did_update_row = False
        for handler in handlers:
            before_update_message = '{}'.format(row)
            if handler.can_provide_radar_id_for_row():
                handler.update_row_for_csv_export(row, self.current_row)
                logging.debug('Handler {} updated row {} from "{}" to "{}"'.format(handler, self.current_row_number, before_update_message, row))
                did_update_row = True
        if not did_update_row:
            return

        self.save_data_to_csv()

    @classmethod
    def configure_argument_parser(cls, parser):
        super(SubcommandCreateRadars, cls).configure_argument_parser(parser)

if __name__ == "__main__":
    system_identifier = radarclient.ClientSystemIdentifier(os.path.basename(sys.argv[0]), radarclient.__version__)
    RadarToolCommandLineDriver.run(extension_namespaces=[globals()], client_system_identifier=system_identifier)

#!/Users/jialongwangsmacbookpro16/Desktop/chatbot/code/bin/python3

# PYTHONPATH=../omniplan-python/ ./omniradar.py
# PYTHONPATH=..:../omniplan-python/: ./bin/radarreporttools.py rtwi
from radarclient.client import AppleDirectoryQuery
from radarclient.model import Relationship
from radarclient.radartool import WebAbstractSubcommand, SubcommandWebIndex, RadarToolCommandLineDriver, ANSIColor, send_mail
import sys
import os
import argparse
import datetime
import bottle
from collections import defaultdict
import plistlib
import json


class ReportToolsWebAbstractSubcommand(WebAbstractSubcommand):

    # this needs to be overridden
    def form_variable_names(self):
       raise Exception("ReportToolsWebAbstractSubcommand is not intended to be used directly. This needs to be subclassed")

    # this needs to be overridden
    def template_variable_names(self):
       raise Exception("ReportToolsWebAbstractSubcommand is not intended to be used directly. This needs to be subclassed")

    # default values for variables only when shouldn't default to an empty string
    def default_variable_values(self):
        return { 'saved_queries': self.queries() }

    # this needs to be overridden
    def create_subject(self):
       raise Exception("ReportToolsWebAbstractSubcommand is not intended to be used directly. This needs to be subclassed")

    # this needs to be overridden
    def run_script_output(self):
       raise Exception("ReportToolsWebAbstractSubcommand is not intended to be used directly. This needs to be subclassed")

    # todo: the script should not need to run again if preview was already generated
    def run_script(self):
        self.run_script_output()
        self.compose_email_and_send()

    def webapp_description(self):
        return { self.webapp_name(): { 'type': self.type,
                    'name': self.name, 'description': self.__doc__ }}

    def property_names(self):
        return self.form_variable_names() + self.template_variable_names()

    def object_variables(self):
        return {k: getattr(self, k, '') for k in self.property_names()}

    def form_variables(self):
        return {k: getattr(self, k, '') for k in self.form_variable_names()}

    def save_query_to_plist(self):
        var_dict = self.form_variables()
        queries = {}
        if os.path.exists(self.fileName()):
            queries = plistlib.readPlist(self.fileName())
        queries[unicode(self.query_name)] = var_dict
        plistlib.writePlist(queries, self.fileName())
        self.update_queries()

    def delete_query_from_plist(self, query):
        queries = {}
        if os.path.exists(self.fileName()):
            queries = plistlib.readPlist(self.fileName())
            if query in queries:
                del queries[query]
                plistlib.writePlist(queries, self.fileName())
        self.update_queries()

    def update_queries(self):
        self.saved_queries = self.queries()

    def queries(self):
        if os.path.exists(self.fileName()):
            return sorted(plistlib.readPlist(self.fileName()).keys())
        else:
            return []

    def set_variables_to_default(self, variables=[]):
        if not variables:
            variables = self.object_variables().keys()

        defaults = self.default_variable_values()
        for variable in variables:
            if variable not in defaults:
                setattr(self, variable, "")
            else:
                setattr(self, variable, defaults[variable])

        self.dsid_list = []
        self.radar_prefixes_list = []

    def set_variables_from_web_ajax(self, params):
        for property_name in params.keys():
            if property_name in self.form_variables():
                value = params[property_name]

                if not value:
                    value = ''

                setattr(self, property_name, value)

        self.set_derived_variables()

    def set_derived_variables(self):
        if self.group_name:
            self.dsid_list = AppleDirectoryQuery.member_dsid_list_for_group_name(self.group_name)

    def handle_auto_complete(self):
        query = bottle.request.params.get('q')
        list = self.radar_client.find_component_bundle_names(query)
        return {'list': sorted(list), 'query': query}

    def handle_save_query(self):
        params = bottle.request.params
        self.set_variables_to_default()
        self.set_variables_from_web_ajax(params)

        self.selected_query = self.query_name
        self.save_query_to_plist()

        return self.updated_query_select_html()

    def handle_load_query(self):
        query = bottle.request.params.get('select_query')
        if os.path.exists(self.fileName()):
            properties = plistlib.readPlist(self.fileName())[query]
            for property_name in self.property_names():
                if property_name in self.form_variable_names() and property_name in properties:
                    setattr(self, property_name, properties[property_name])
        self.selected_query = query
        return_variables = self.object_variables()
        return return_variables

    def handle_preview(self):
        params = bottle.request.params
        self.set_variables_to_default()
        self.set_variables_from_web_ajax(params)

        self.subject = self.create_subject()
        self.run_script_output()
        self.preview_html_code = self.output
        return_variables = self.object_variables()
        return return_variables

    def handle_delete_query(self):
        query = bottle.request.params.get('select_query')
        self.delete_query_from_plist(query)
        return self.updated_query_select_html()

    def handle_send(self):
        params = bottle.request.params
        self.set_variables_to_default()
        self.set_variables_from_web_ajax(params)

        self.subject = self.create_subject()
        self.run_script()
        return_variables = {'from_email': self.from_email, 'to_email': self.to_email, 'subject': self.subject }
        return return_variables

    def updated_query_select_html(self):
        template_values = {
            'saved_queries': self.saved_queries
        }
        html_result = bottle.template(self.template_name(suffix='selectquery'), template_values)
        return html_result

    def index(self):
        preview = getattr(self, 'preview_html_code', '')
        self.set_variables_to_default()

        result = bottle.template(self.template_name(), self.object_variables())
        return result

    def fileName(self):
        return os.path.join(os.environ['HOME'], u'Library/Preferences/com.apple.radartools.{}.plist'.format(self.webapp_name()))

    def configure_webapp(self, app):
#         os.system('sleep 1; open http://localhost:8080')

        self.queries()


class SubcommandReportForMilestone(ReportToolsWebAbstractSubcommand):
    """Produce a report containing number of bugs per assignee for a particular milestone"""

    def __init__(self, *args):
        super(SubcommandReportForMilestone, self).__init__(*args)

        self.milestone = ''
        self.include_unscreened = False
        self.list_unscreened = False
        self.component_bundle = ''
        self.team_name = ''

        self.radars = []
        self.people = {}
        self.radars_for_person = {}

        self.unscreened_radars = []
        self.people_unscreened = {}
        self.unscreened_for_person = {}

        self.to_email = ''
        self.from_email = ''
        self.output = ''

        self.dsid_list = []

    def setup(self, milestone, group_name, include_unscreened, list_unscreened, component_bundle, from_email, to_email, team_name):
        self.milestone = milestone
        self.include_unscreened = include_unscreened
        self.list_unscreened = list_unscreened
        self.component_bundle = component_bundle
        self.to_email = to_email
        self.from_email = from_email
        self.team_name = team_name

        if group_name:
            self.dsid_list = AppleDirectoryQuery.member_dsid_list_for_group_name(group_name)

    def request_radars_in_milestone(self):
        request_data = { "componentBundle" : { "name" : self.component_bundle },
					 "milestone" : self.milestone, "state": "Analyze" }
        self.radars = self.radar_client.find_problems(request_data, batch_size=10, progress_callback=self.progress_callback)
        self.people, self.radars_for_person = self.find_people_from_ids(self.radars)

    def request_unscreened_radars(self):
        if self.include_unscreened:
            request_data = { "componentBundle" : { "name" : self.component_bundle },
					 "milestone" : None, "state": "Analyze" }
            self.unscreened_radars = self.radar_client.find_problems(request_data, batch_size=10, progress_callback=self.progress_callback)
            self.people_unscreened, self.unscreened_for_person = self.find_people_from_ids(self.unscreened_radars)

    def string_for_unscreened_list(self):
        unscreened_output = "<b>Unscreened</b><br><br><table id='unscreened-table'>"

        unscreened_output += u'<tr><td><b>Total</b></td><td></td><td><a href={0}>{1}</a></td></tr>'.format(
                    self.radar_list_url(self.unscreened_radars), str(len(self.unscreened_radars)))

        for person in self.people_unscreened:

            # if person is not in the team don't display an output
            if self.dsid_list and not person in self.dsid_list:
                continue

            unscreened_list = self.unscreened_for_person[person]
            unscreened_output += u'<tr><td>{0} {1}</td><td></td><td><a href={2}>{3}</a></td></tr>'.format(
                    self.people_unscreened[person].firstName, self.people_unscreened[person].lastName,
                    self.radar_list_url(unscreened_list), len(unscreened_list))

        unscreened_output += "</table><br>- - - - - - - - -"

        return unscreened_output

    def string_for_radar_list(self):
        radar_list = []
        people_keys = self.people.keys()

        # sort people to show team first
        if self.dsid_list:
            dsid_list_comparator = lambda x, y: cmp((x in self.dsid_list), (y in self.dsid_list))
            people_keys.sort(dsid_list_comparator)
            people_keys.reverse()

        for person in people_keys:

            priority_comparator = lambda x, y: cmp(x.priority, y.priority)
            self.radars_for_person[person].sort(priority_comparator)

            output_radars_list = []
            if person in self.radars_for_person:
                for radar in self.radars_for_person[person]:
                    output_radars_list.append(u'P{0} - (<a href=rdar://problem/{1}>{1}</a>) {2}'.format(radar.priority, radar.id, radar.title))

            if output_radars_list:
                output_radars = "<br>" + '<br>\n'.join(output_radars_list)
            else:
                output_radars = ""

            radar_list.append(u'<b>{} {}</b>{}'.format(self.people[person].firstName, \
                            self.people[person].lastName, output_radars))

        return '<br><br>'.join(radar_list)

    def create_subject(self):
        today = datetime.date.today()
        dateToday = str(today.month)+"/"+str(today.day)

        if self.team_name:
            return self.team_name + " - " + self.milestone+" bugs - "+dateToday
        else:
            return self.milestone+" bugs - "+dateToday

    def compose_email_and_send(self):
        subject = self.create_subject()
        send_mail(self.from_email, self.to_email, subject, self.output)

    def generate_output(self):
        output = []

        total_in_milestone = u'<b>Total in {}: </b><a href="{}">{}</a>'.format(
                             self.milestone, self.radar_list_url(self.radars), len(self.radars))

        # include unscreened per person
        if self.include_unscreened and self.list_unscreened and self.unscreened_radars:
            unscreened_output = self.string_for_unscreened_list()
            output.append(unscreened_output)
            output.append(total_in_milestone)

        # include only unscreened total
        elif self.include_unscreened and self.unscreened_radars:
            output.append(total_in_milestone)
            output.append(u'<b>Total unscreened: </b><a href="{}">{}</a>'.format(
                                                self.radar_list_url(self.unscreened_radars),
                                                len(self.unscreened_radars)))
            output.append("- - - - - - - - -")

        # unscreened included but is 0
        elif self.include_unscreened:
            output.append(total_in_milestone)
            output.append("<b>Total unscreened:</b> 0")
            output.append("- - - - - - - - -")

        # only total for the specified milestone
        else:
            output.append(total_in_milestone)

        # include list of radars per person
        radar_output = self.string_for_radar_list()
        output.append(radar_output)

        self.output = '<br><br>'.join(output)

    def __call__(self):

        self.setup(self.args.milestone, self.args.team_apple_directory_name,
                    self.args.include_unscreened, self.args.list_unscreened_per_person,
                    self.args.component_bundle, self.args.sender, self.args.recipients, self.args.team_name)

        self.request_radars_in_milestone()

        self.request_unscreened_radars()

        self.generate_output()

        self.compose_email_and_send()

    @classmethod
    def configure_argument_parser(cls, parser):
        parser.add_argument('--sender', help='E-mail address from which report will be sent')
        parser.add_argument('--recipients', help='E-mail address(es) to which report will be sent')
        parser.add_argument('--milestone', help='The milestone to search for')
        parser.add_argument('--component_bundle', help='The component_bundle to search for. This is case sensitive.')
        parser.add_argument('--team_apple_directory_name', required=False, help="Team's Apple Directory name")
        parser.add_argument('--team_name', required=False, help='The name of the team for which the report is being generated for.')
        parser.add_argument('--include_unscreened', action='store_true', help='Whether unscreened bugs should be included: true / false')
        parser.add_argument('--list_unscreened_per_person', action='store_true', help='Whether unscreened bugs should be tracked per person: true / false')


class SubcommandWebReportForMilestone(SubcommandReportForMilestone):

    '''shows all radars per assignee for a particular component bundle and milestone.
	there's an additional option to show unscreened bugs as well.'''

    type = 'report'
    name = 'radars per assignee for milestone'

    @classmethod
    def configure_argument_parser(cls, parser):
        pass

    def run_script_output(self):
        self.request_radars_in_milestone()
        self.request_unscreened_radars()
        self.generate_output()

    def form_variable_names(self):
        return """from_email to_email team_name milestone component_bundle group_name
            include_unscreened list_unscreened query_name""".split()

    def template_variable_names(self):
        return """subject saved_queries preview_html_code""".split()

    def __call__(self):
        self.run_default_webapp()

    def configure_default_routes(self, app):
        super(SubcommandWebReportForMilestone, self).configure_default_routes(app)
        app.route('/auto_complete/', method=['GET'], callback=self.handle_auto_complete)
        app.route('/save_query/', method=['GET'], callback=self.handle_save_query)
        app.route('/load_query/', method=['GET'], callback=self.handle_load_query)
        app.route('/preview/', method=['GET'], callback=self.handle_preview)
        app.route('/delete_query/', method=['GET'], callback=self.handle_delete_query)
        app.route('/send/', method=['GET'], callback=self.handle_send)


class SubcommandReportForUpcomingFeatures(ReportToolsWebAbstractSubcommand):
    """Produce a report containing upcoming TLFs and sub-TLFs per engineer"""

    def __init__(self, *args):
        super(SubcommandReportForUpcomingFeatures, self).__init__(*args)
        self.milestones = []
        self.component_bundle = ''
        self.team_name = ''

        self.radar_prefixes = ''
        self.radar_prefixes_list = []

        self.radars = []
        self.people = {}
        self.radars_for_person = {}
        self.days = 14

        self.to_email = ''
        self.from_email = ''
        self.output = ''

        self.inclue_no_dates = False

        self.dsid_list = []

    def radars_in_milestones_progress_callback(self, progress, loaded_count=None, total_count=None):
        self.update_progress(progress*.3)

    def request_radars_in_milestones(self):

        titles = [x+'%' for x in self.radar_prefixes_list]

        request_data = { "componentBundle" : { "name" : self.component_bundle },
        				 "milestone" : self.milestones, "state": "Analyze",
        				 "title": { "any" : titles } }

        if not titles:
            del request_data['title']
        self.radars = self.radar_client.find_problems(request_data, ['targetStartDate', 'targetCompletionCurrent'], batch_size=10, progress_callback=self.radars_in_milestones_progress_callback)
        print('done in request radars in milestone')
        self.people, self.radars_for_person = self.find_people_from_ids(self.radars)

    def filter_radars(self):
        # removes anyone who is not in the apple directory group

        if self.start_or_end_date == 'start':
            use_start_date = True
        else:
            use_start_date = False

        num_of_discarded_radars = 0
        if self.dsid_list:
            for person in [x for x in self.people if not x in self.dsid_list]:
                num_of_discarded_radars += len(self.radars_for_person[person])
                del self.people[person]

        today = datetime.date.today()

        remove_people = []

        progress = .3

        num_radars_to_filter = len(self.radars) - num_of_discarded_radars
        progress_increment = (1-progress)/(num_radars_to_filter if num_radars_to_filter else 1)

        # cleaning up radars - remove TLFs that have subtasks and ones that are not starting within <days>, when dates are required
        for person in self.people:
            radars = []
            for radar in self.radars_for_person[person]:
                date = radar.targetStartDate if use_start_date else radar.targetCompletionCurrent

                if (self.include_no_dates and not date) or (date and (date - today) < datetime.timedelta(int(self.days))):
                    radars.append(radar)

                progress += progress_increment
                self.update_progress(progress)

            self.radars_for_person[person] = radars
            if not radars:
                remove_people.append(person)

        for person in remove_people:
            del self.people[person]

    def generate_output(self):
        output = []

        today = datetime.date.today()

        for person in self.people:
            output_radars = []

            # sort radars by expected completion date
            mindate = datetime.date(datetime.MINYEAR, 1, 1)
            self.radars_for_person[person] = sorted(self.radars_for_person[person], key=(lambda x: x.targetCompletionCurrent or mindate), reverse=False)

            for radar in self.radars_for_person[person]:

                completion_date = radar.targetCompletionCurrent
                date = ''
                if completion_date:
                    date = str(completion_date.month) + '/' + str(completion_date.day)
                else:
                    date = 'TBD'

                color = 'red' if (completion_date and (completion_date < today)) or date == 'TBD' else 'black'

                output_radars.append(u'<font color={3}>{0}</font>&nbsp;&nbsp;&nbsp;&nbsp;{1} (<a href="rdar://problem/{2}">{2}</a>)'.format(date,
                                    radar.title, str(radar.id), color))

                tabs_with_emdash = '&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&mdash; '

                if self.include_blocked_by_radars:
                    for related_radar in radar.related_radars([Relationship.TYPE_BLOCKED_BY]):
                        if related_radar.state == 'Analyze':
                            related_radar_date = related_radar.targetCompletionCurrent
                            if not related_radar_date:
                                related_radar_date = 'TBD'
                            else:
                                related_radar_date = str(related_radar_date.month) + '/' + str(related_radar_date.day)
                            output_radars.append(u'{0}Blocked by: {1} (<a href="rdar://problem/{2}">{2}</a>) (ETA: {3})'.format(tabs_with_emdash, related_radar.title, str(related_radar.id), related_radar_date))

                if self.note_radars_in_review:
                    if radar.substate == 'Review':
                        output_radars.append(u'{0}Radar is out for review'.format(tabs_with_emdash))

            output.append(u'<b>{0} {1}</b><br><div >{2}</div>'.format(self.people[person].firstName, \
                            self.people[person].lastName, '</div><div>'.join(output_radars)))

        self.output = '<br>'.join(output)

    def create_subject(self):
        today = datetime.date.today()
        dateToday = str(today.month)+"/"+str(today.day)
        milestones_formatted = " / ".join(self.milestones)

        if self.team_name:
            return u'{} - {} schedule ({})'.format(self.team_name, milestones_formatted, dateToday)
        else:
            return u'{} schedule ({})'.format(milestones_formatted, dateToday)

    def compose_email_and_send(self):
        subject = self.create_subject()
        send_mail(self.from_email, self.to_email, subject, self.output)

    def __call__(self):
        self.milestones = self.args.milestones
        self.component_bundle = self.args.component_bundle
        self.team_name = self.args.team_name
        self.days = self.args.days
        self.from_email = self.args.sender
        self.to_email = self.args.recipients

        group_name = self.args.team_apple_directory_name
        if group_name:
            self.dsid_list = AppleDirectoryQuery.member_dsid_list_for_group_name(group_name)

        self.request_radars_in_milestones()
        self.filter_radars()
        self.generate_output()
        self.compose_email_and_send()

    @classmethod
    def configure_argument_parser(cls, parser):
        parser.add_argument('--sender', help='E-mail address from which report will be sent')
        parser.add_argument('--recipients', help='E-mail addresses to which report will be sent')
        parser.add_argument('--milestone', help='The milestone to search for')
        parser.add_argument('--component_bundle', help='The component_bundle to search for. This is case sensitive')
        parser.add_argument('--days', required=False, type=int, default=14, help='How far out the schedule should go')
        parser.add_argument('--team_apple_directory_name', required=False, help="Team's Apple Directory name")
        parser.add_argument('--team_name', required=False, help='The name of the team for which the report is being generated for')


class SubcommandWebReportForUpcomingFeatures(SubcommandReportForUpcomingFeatures):
    '''shows all the upcoming TLFs and sub-TLFs to be started in the next two weeks
    (or however many days specified) with due date per assignee. if the group name is
    specified, results will be limited to only those individuals.'''

    type = 'report'
    name = 'upcoming TLFs and sub-TLFs per assignee'

    @classmethod
    def configure_argument_parser(cls, parser):
        pass

    def run_script_output(self):
        self.request_radars_in_milestones()
        if self.radars:
            self.filter_radars()
        self.generate_output()

    def form_variable_names(self):
        return """from_email to_email team_name milestone component_bundle group_name days
            radar_prefixes start_or_end_date include_no_dates include_blocked_by_radars note_radars_in_review query_name""".split()

    def template_variable_names(self):
        return """subject saved_queries preview_html_code""".split()

    def default_variable_values(self):
        return { 'saved_queries': self.queries(), 'days': 14 }

    def set_derived_variables(self):
        if self.group_name:
            self.dsid_list = AppleDirectoryQuery.member_dsid_list_for_group_name(self.group_name)

        if self.radar_prefixes:
            self.radar_prefixes_list = [x.strip() for x in self.radar_prefixes.split(', ')]

        self.milestones = self.milestone.split(", ")

    def configure_default_routes(self, app):
        super(SubcommandWebReportForUpcomingFeatures, self).configure_default_routes(app)
        app.route('/auto_complete/', method=['GET'], callback=self.handle_auto_complete)
        app.route('/save_query/', method=['GET'], callback=self.handle_save_query)
        app.route('/load_query/', method=['GET'], callback=self.handle_load_query)
        app.route('/preview/', method=['GET'], callback=self.handle_preview)
        app.route('/delete_query/', method=['GET'], callback=self.handle_delete_query)
        app.route('/send/', method=['GET'], callback=self.handle_send)

class RadarFeatureTraversalDelegate(object):
    """
    Implementation of all_relationships delegate. Only includes subtask and blocking radars
    that are in analyze.

    """
    def __init__(self, accepted_states, accepted_relationships):
        self.accepted_states = accepted_states
        self.accepted_relationships = accepted_relationships

    def should_follow_relationship(self, relationship):
        return relationship.related_radar.state in self.accepted_states and relationship.type in self.accepted_relationships


class SubcommandReportForFeatures(ReportToolsWebAbstractSubcommand):

    def __call__(self):
        radar_ids = self.args.radars
        delegate = RadarFeatureTraversalDelegate(['Analyze', 'Integrate'], [Relationship.TYPE_PARENT_OF, Relationship.TYPE_BLOCKED_BY])
        radars = defaultdict(lambda: defaultdict(set))

        for radar_id in radar_ids:
            radar = self.radar_client.radar_for_id(radar_id)

            for relationship_list in radar.all_relationships(delegate=delegate):
                for relationship in relationship_list:
                    radar = relationship.radar
                    related_radar = relationship.related_radar

                    if relationship.type == Relationship.TYPE_PARENT_OF:
                        radars[radar.id]['children'].add(related_radar)
                        radars[related_radar.id]['parents'].add(radar)

                    if relationship.type == Relationship.TYPE_BLOCKED_BY:
                        parents = radars[radar.id]['parents'].copy()

                        for parent in parents:
                            radars[parent.id]['children'].add(related_radar)
                            radars[related_radar.id]['parents'].add(parent)
                            radars[radar.id]['parents'].remove(parent)

                        radars[related_radar.id]['children'].add(radar)
                        radars[related_radar.id]['blocker'] = True

            self.print_radars(radars, radar.id, '.')

    def print_radars(self, dict, key, dots):
        children = dict[key]['children']

        for child in children:
            print(dots + str(child.id))
            self.print_radars(dict, child.id, dots+'.')

    @classmethod
    def configure_argument_parser(cls, parser):
        parser.add_argument('--radars', nargs='+', help='List of radars for which to find tree of children and blocking radars')

class SubcommandWebScriptForCreatingRadars(ReportToolsWebAbstractSubcommand):
    '''create multiple radars at once. specify the parameters to be applied to all the radars,
    including radar relationships.'''

    type = 'script'
    name = 'create multiple radars'

    def index(self):
        template_name = u'{}/{}'.format(self.web_resources_root(), self.subcommand_name())
        result = bottle.template(template_name, self.object_variables())
        return result

    def form_variable_names(self):
        return '''radar_names component_name component_version milestone priority
            classification reproducible'''.split()

    def template_variable_names(self):
        return []

    def relationship_convertor(self):
        return {
            'Related to': Relationship.TYPE_RELATED_TO,
            'Blocked by': Relationship.TYPE_BLOCKED_BY,
            'Blocking': Relationship.TYPE_BLOCKING,
            'Parent of': Relationship.TYPE_PARENT_OF,
            'Subtask of': Relationship.TYPE_SUBTASK_OF
        }

    def handle_send(self):
        params = bottle.request.params
        params_data = json.loads(params.keys()[0])
        radars = params_data['radars']
        email = params_data['email']

        created_radars = []

        for radar_data in radars:
            data = {
                'title': radar_data['title'],
                'component': {'name': radar_data['component_name'], 'version': radar_data['component_version']},
                'description': radar_data['description'],
                'classification': radar_data['classification'],
                'reproducible': radar_data['reproducible'],
            }
            radar = self.radar_client.create_radar(data)

            if radar_data['milestone']:
                radar.milestone = radar_data['milestone']

            radar.priority = radar_data['priority']

            if radar_data['related_id']:
                related_radar = self.radar_client.radar_for_id(radar_data['related_id'])
                relationship = Relationship(
                    self.relationship_convertor()[radar_data['related_type']], radar, related_radar
                )
                radar.add_relationship(relationship)

            radar.commit_changes()

            created_radars.append(radar)

        template_values = {
            'created_radars': created_radars
        }
        html_result = bottle.template(self.template_name(suffix='created-radars'), template_values)

        from_email = email['from_email']
        to_email = email['to_email']
        subject = email['subject']

        if from_email and to_email:
            send_mail(from_email, to_email, subject, 'The following radars were created:<br/><br/>' + html_result)

        return html_result

    def configure_default_routes(self, app):
        super(SubcommandWebScriptForCreatingRadars, self).configure_default_routes(app)
        app.route('/send/', method=['POST'], callback=self.handle_send)

class SubcommandReportToolsWebIndex(SubcommandWebIndex):

    def subcommand_classes(self):
        subcommand_list = [SubcommandWebReportForMilestone, SubcommandWebReportForUpcomingFeatures,
            SubcommandWebScriptForCreatingRadars]

        try:
            from omniradar import SubcommandWebSyncDates
            subcommand_list.append(SubcommandWebSyncDates)
        except Exception as e:
            print('skipping import: {}'.format(e))
            pass

        return subcommand_list

if __name__ == "__main__":
    RadarToolCommandLineDriver.run(extension_namespaces=[globals()])

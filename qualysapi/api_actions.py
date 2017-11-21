from lxml import objectify, etree
from lxml.etree import fromstring, tostring
import xmljson
from qualysapi.api_objects import *
from qualysapi.exceptions import *
from qualysapi.util import date_param_format, qualys_datetime_to_python

logger = logging.getLogger(__name__)
import pprint

# from multiprocessing import pool, Event

# from threading import Thread, Event
import threading


# two essential methods here include creating a semaphore-based local threading
# or multiprocessing pool which is capable of monitoring and dispatching
# callbacks to calling instances when both parsing and consumption complete.

# In the default implementation the calls are blocking and perform a single
# request, parse the response, and then wait for parse consumption to finish.
# This isn't ideal, however, as there are often cases where multiple requests
# could be sent off at the same time and handled asynchronously.  The
# methods below wrap thread pools or process pools for asynchronous
# multi-request parsing/consuming by a single calling program.


class QGActions(object):
    import_buffer = None
    buffer_prototype = None
    request = None
    stream_request = None

    conn = None

    def __init__(self, *args, **kwargs):
        '''
        Set up the Actions connection wrapper class

        @Params
        :parameter cache_connection:
            either this option or the connection option are
        required, but this one takes precedence.  If you specify a cache
        connection then the connection is inferred from the cache
        configuration.
        :parameter connection:
            required if no cache_connection is specified, otherwise
        it is ignored in favor of the cache connection.
        '''
        self.conn = kwargs.get('cache_connection', None)
        if self.conn:
            self.request = self.conn.cache_request
            self.stream_request = self.conn.stream_cache_request
        else:
            self.conn = kwargs.get('connection', None)
            if not self.conn:
                raise NoConnectionError('You attempted to make an \
api requst without specifying an API connection first.')
            self.request = self.conn.request
            self.stream_request = self.conn.stream_request

    def clone(self, proto=None):
        # TODO: this is wrong...  fix it.
        if proto == None:
            conf = self.conn.getConfig()
            return QGActions(
                conf.get_auth(),
                hostname=conf.get_hostname(),
                proxies=conf.proxies,
                max_retries=conf.max_retries)
        else:
            if not isinstance(QGActions, proto):
                raise exceptions.QualysFrameworkException('Cannot clone, \
prototype is not an instance.')
            else:
                return proto(
                    conf.get_auth(),
                    hostname=conf.get_hostname(),
                    proxies=conf.proxies,
                    max_retries=conf.max_retries)

    def parseResponse(self, **kwargs):
        """parseResponse
        inline parsing for requests.  Part of the overhaul.
        :param **kwargs:
        """
        exit = kwargs.pop('exit', threading.Event())
        source = kwargs.pop('source', None)
        if not source:
            raise QualysException('No source file or URL or raw stream found.')

        # select the response file-like object
        response = None
        if isinstance(source, str):
            response = self.stream_request(source, **kwargs)
        else:
            response = source

        if self.import_buffer is None:
            if self.buffer_prototype is None:
                self.import_buffer = ImportBuffer()
            else:
                self.import_buffer = self.buffer_prototype()
        rstub = None
        if 'report' in kwargs:
            rstub = kwargs.get('report')
            if not isinstance(rstub, Report):
                raise exceptions.QualysFrameworkException('Only Report objects'
                                                          ' and subclasses can be passed to this function as reports.')

        context = etree.iterparse(response, events=('end',))
        # optional default elem/obj mapping override
        local_elem_map = kwargs.get('obj_elem_map', obj_elem_map)
        for event, elem in context:
            if exit.is_set():
                logger.info('Exit event caused immediate return.')
                break
            # Use QName to avoid specifying or stripping the namespace, which we don't need
            stag = etree.QName(elem.tag).localname.upper()
            if stag in local_elem_map:
                self.import_buffer.add(obj_elem_map[stag](elem=elem,
                                                          report_stub=rstub))
                # elem.clear() #don't fill up a dom we don't need.
        results = self.import_buffer.finish(**kwargs)
        # TODO: redress
        self.checkResults(results)
        # special case: report encapsulization...
        return results

    def checkResults(self, results):
        '''check for actionable response errors'''
        api_response = None
        if results is None:
            raise exceptions.QualysFrameworkException('Got a NoneType from the \
parser.')
        elif not results:
            logger.debug('Empty result list from parser.')
        if isinstance(results, list) and \
                        len(results) > 0 and \
                issubclass(type(results[0]), SimpleReturn):
            api_response = results[0]
        elif issubclass(type(results), SimpleReturn):
            api_response = results

        if api_response:
            api_response.raiseAPIExceptions()
        return results

    def getHost(host):
        call = '/api/2.0/fo/asset/host/'
        parameters = {'action': 'list', 'ips': host, 'details': 'All'}
        hostData = objectify.fromstring(self.request(call, data=parameters)).RESPONSE
        try:
            hostData = hostData.HOST_LIST.HOST
            return Host(hostData.DNS, hostData.ID, hostData.IP, hostData.LAST_VULN_SCAN_DATETIME, hostData.NETBIOS,
                        hostData.OS, hostData.TRACKING_METHOD)
        except AttributeError:
            return Host("", "", host, "never", "", "", "")

    def getHostRange(self, start, end):
        call = '/api/2.0/fo/asset/host/'
        parameters = {'action': 'list', 'ips': start + '-' + end}
        hostData = objectify.fromstring(self.request(call, data=parameters))
        hostArray = []
        for host in hostData.RESPONSE.HOST_LIST.HOST:
            hostArray.append(Host(host.DNS, host.ID, host.IP, host.LAST_VULN_SCAN_DATETIME, host.NETBIOS, host.OS,
                                  host.TRACKING_METHOD))

        return hostArray

    def listAssetGroups(self, groupName=''):
        call = 'asset_group_list.php'
        if groupName == '':
            agData = objectify.fromstring(self.request(call))
        else:
            agData = objectify.fromstring(self.request(call, 'title=' + groupName)).RESPONSE

        groupsArray = []
        scanipsArray = []
        scandnsArray = []
        scannersArray = []
        for group in agData.ASSET_GROUP:
            try:
                for scanip in group.SCANIPS:
                    scanipsArray.append(scanip.IP)
            except AttributeError:
                scanipsArray = []  # No IPs defined to scan.

            try:
                for scanner in group.SCANNER_APPLIANCES.SCANNER_APPLIANCE:
                    scannersArray.append(scanner.SCANNER_APPLIANCE_NAME)
            except AttributeError:
                scannersArray = []  # No scanner appliances defined for this group.

            try:
                for dnsName in group.SCANDNS:
                    scandnsArray.append(dnsName.DNS)
            except AttributeError:
                scandnsArray = []  # No DNS names assigned to group.

            groupsArray.append(
                AssetGroup(group.BUSINESS_IMPACT, group.ID, group.LAST_UPDATE, scanipsArray, scandnsArray,
                           scannersArray, group.TITLE))

        return groupsArray

    # single-thread/process specific 1-off query for starting a map report
    def startMapReportOnMap(self, mapr, **kwargs):
        '''Generates a report on a map.
        Parameters:
        :parameter mapr:
            the map result to generate a report against.  Can be a string
        map_ref but a map result object is really preferred.
        :parameter domain:
            one of domain or ip_restriction are required for map reports.
        You can use the asset domain list for this parameter.  If this
        parameter is excluded 'none' is substituted but a lack of an IP range
        list will result in an api exception.
        :parameter ip_restriction:
            Either a string of ips acceptable to qualys or a list
        of IP range objects.  These objects provide a reasonably uniform way to
        specify ranges.
        :parameter template_id:
            (Optional) the report template ID to use.  Required.
        :parameter template_name:
            (Optional) the name of the template to use. (look
        up ID)
        :parameter use_default_template:
            (Optional) boolean.  Look up the
        default map report template and load the template_id from it.
        Note: If none of the above are sent then the configuration option
        default template is used.  That will either be 'Unknown Device Report'
        or whatever you have in your config for the map_template configuration
        option under the report_templates configuration section.

        :parameter report_title:
            (Optional) Specify a name for this report.
        :parameter output_format:
            (Optional) Default is xml.  Options are pdf, html,
        mht, xml or csv.  This API only supports parsing of xml format, the
        rest must be downloaded and saved or viewed.
        :parameter hide_header:
            (Optional) Tell the API to remove report header info.
        Optional.  By default this isn't set at all.
        :parameter comp_mapr:
            (Optional) A map result to compare against.

        Return tuple (mapr, report_id):
            if mapr is a map result object, the report_id property will be set.
            Either way, a tuple is returned with mapr and report_id at 0,1
            respectively.
        '''

        # figure out our template_id
        template_id = 0
        if 'template_id' in kwargs:
            template_id = kwargs.get('template_id', 0)
        elif 'template_name' in kwargs or kwargs.get('use_default_template',
                                                     False):
            # get the list of tempaltes
            template_list = self.listReportTemplates()
            use_default_template = kwargs.get('use_default_template', False)
            template_title = kwargs.get('template_title',
                                        self.conn.getConfig().getReportTemplate())
            for template in template_list:
                if use_default_template and \
                        template.is_default and \
                                template.report_type == 'Map':
                    template_id = template.template_id
                elif template.title == template_title:
                    tempalte_id = template.template_id
                if not template_id:  # false if not 0
                    break
        else:
            raise exceptions.QualysFrameworkException('You need one of a \
                    template_id, template_name or use_default_template to \
                    generate a report from a map result.')

        report_title = kwargs.pop('report_title', None)
        comp_mapr = kwargs.pop('comp_mapr', None)
        if not report_title:
            mapr_name = mapr.name if not isinstance(mapr, str) else str(mapr)
            comp_mapr_name = None
            if comp_mapr:
                comp_mapr_name = comp_mapr.name if not isinstance(comp_mapr, \
                                                                  str) else str(comp_mapr)

            report_title = '%s - api generated' % (mapr_name)
            if comp_mapr_name:
                report_title = '%s vs. %s' % (comp_mapr_name, report_title)

        output_format = kwargs.pop('output_format', 'xml')

        call = '/api/2.0/fo/report/'
        params = {
            'action': 'launch',
            'template_id': template_id,
            'report_title': report_title,
            'output_format': output_format,
            'report_type': 'Map',
            'domain': kwargs.pop('domain', 'none'),
        }

        if 'hide_header' in kwargs:
            # accept boolean type or direct parameter
            if isinstance(kwargs.get('hide_header'), str):
                params['hide_header'] = kwargs.get('hide_header')
            else:
                params['hide_header'] = '0' if not kwargs.get('hide_header') \
                    else '1'

        if 'ip_restriction' in kwargs:
            if isinstance(kwargs.get('ip_restriction'), str):
                params['ip_restriction'] = kwargs.pop('ip_restriction')
            else:
                params['ip_restriction'] = ','.join((
                    str(iprange) for iprange in
                    kwargs.pop('ip_restriction')))
        elif params['domain'] == 'none':
            raise exceptions.QualysException('Map reports require either a \
            domain name or an ip_restriction collection of IPs and/or ranges. \
            You specified no domain and no ips.')

        params['report_refs'] = mapr.ref if not isinstance(mapr, str) else \
            str(mapr)

        if comp_mapr:
            params['report_refs'] = '%s,%s' % (params['report_refs'], \
                                               comp_mapr.ref if not isinstance(comp_mapr, str) else \
                                                   str(comp_mapr))

        response = self.parseResponse(source=call, data=params)
        if not len(response) and isinstance(response[0], SimpleReturn):
            response = response[0]
            if response.hasItem('ID'):
                report_id = response.getItemValue('ID')
                if not isinstance(mapr, str):
                    mapr.report_id = report_id
                return (mapr, report_id)
        # if we get here, something is wrong.
        raise exceptions.QualysFrameworkException('Unexpected API '
                                                  'response.\n%s' % (pprint.pformat(response)))

    def fetchReport(self, rid=None, report=None, consumer_prototype=None,
                    **kwargs):
        '''
        Uses the cache to quickly look up the report associated with a specific
        map ref.
        This API only handles XML.  Anything else you're on your own other than
        using this API to download the report.

        :param rid:
            the report_id for the report to fetch
        :param report:
            the report header info/skeleton (Report object).  This can contain
            the report id rather than the rid named argument.
        :param consumer_prototype:
            Optional.  A subclass prototype of BufferConsumer which will act on
            each report item in parallel with the downloading of the report.
        :param **kwargs:
            expansion arguments
        '''
        if rid is None and report is None:
            raise exceptions.QualysException('A report id is required.')
        elif rid is None:
            rid = report.id

        call = '/api/2.0/fo/report/'
        params = {
            'action': 'fetch',
            'id': rid
        }
        # return 1 or None.  API doesn't allow multiple.  Also make sure it's a
        # report and not a SimpleReturn (which can happen)
        results = self.parseResponse(source=call, data=params, report=report,
                                     consumer_prototype=consumer_prototype)
        for result in results:
            if isinstance(result, Report):
                return result
                # None result

    def searchReport(self, consumer_prototype=None, **kwargs):
        optional_params = [
            ('action', 'search'),
            ('output_format', 'xml'),
            ('tracking_method', None),
            ('ips', None),
            ('ips_network_id', None),
            ('asset_group_ids', None),
            ('asset_groups', None),
            ('assets_in_my_network_only', None),
            ('ec2_instance_status', None),
            ('ec2_instance_id', None),
            ('ec2_instance_id_modifier', None),
            ('display_ag_titles', None),
            ('ports', None),
            ('services', None),
            ('qids', None),
            ('qid_with_text', None),
            ('qid_with_modifier', None),
            ('use_tags', None),
            ('tag_set_by', None),
            ('tag_include_selector', None),
            ('tag_exclude_selector', None),
            ('tag_set_include', None),
            ('tag_set_exclude', None),
            ('first_found_days', None),
            ('first_found_modifier', None),
            ('last_vm_scan_days', None),
            ('last_vm_scan_modifier', None),
            ('last_pc_scan_days', None),
            ('last_pc_scan_modifier', None),
            ('dns_name', None),
            ('dns_modifier', None),
            ('netbios_name', None),
            ('netbios_modifier', None),
            ('os_cpe_name', None),
            ('os_cpe_modifier', None),
            ('os_name', None),
            ('os_modifier', None)
        ]
        call = '/api/2.0/fo/report/asset/'
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # return 1 or None.  API doesn't allow multiple.  Also make sure it's a
        # report and not a SimpleReturn (which can happen)
        return self.parseResponse(source=call, data=params,
                                  consumer_prototype=consumer_prototype,
                                  obj_elem_map={
                                      'HOST': Host,
                                      'WARNING': AssetWarning,
                                  },
                                  **kwargs)

    def queryQKB(self, consumer_prototype=None, **kwargs):
        '''
        Pulls down a set of Qualys Knowledge Base entries in XML and hands them
        off to the parser/consumer framework.

        Params:

        :parameter ids:
            a list of Qualys QIDs to pull QKB entries for.  Limits the
            result set.  Can be empty or none if pulling all.
        :parameter all:
            boolean.  Causes qids to be ignored if set.  Pulls the entire
            knowledge base.
        :parameter last_modified_after:
            an inclusive subset of new and modified entries since
            a specific date.  Can be a datetime (which will be converted to a
            string query parameter) or a string formatted as Qualys expects
            .  It is up to the calling function to ensure strings are correct if
            you choose to use them.  This brackets all of the XX_after variables.
        :parameter last_modified_before:
            an inclusive subset old entries.  This brackets all
            of the XX_before variables.
        :parameter details:
            defaults to 'All' but you can specify 'Basic' or 'None'.
        :parameter range:
            A tuple of qids.  (Min,Max).  Shorthand for a specific list.
        :parameter only_patchable:
            Boolean.  Limits the results to only QKB entries that
            have known patches.
        :parameter show_pci_reasons:
            False by default.  You have to have this in your
            sub for it to be safe.
        :parameter file:
            a special (but useful) case in which a file should be used to
            load the input.  In this case the entire file is parsed, regardless
            of the other parameters.
        :parameter discovery_method:
            'RemoteAndAuthenticated' by default, but valid
            :options:
                -'Remote'
                -'Authenticated'
                -'RemoteOnly'
                -'AuthenticatedOnly'
                -'RemoteAndAuthenticated'

        Retuns of this function depend on the parse consumers.  A list of
        objects or None.
        '''
        optional_params = [
            ('action', 'list'),
            ('echo_request', '0'),  #: optional but default
            ('details', None),  #: {Basic|All| None }
            ('ids', None),  #: {value}
            ('id_min', None),  #: {value}
            ('id_max', None),  #: {value}
            ('is_patchable', None),  #: {0|1}
            ('last_modified_after', None),  #: {date}
            ('last_modified_before', None),  #: {date}
            ('last_modified_by_user_after', None),  #: {date}
            ('last_modified_by_user_before', None),  #: {date}
            ('last_modified_by_service_after', None),  #: {date}
            ('last_modified_by_service_before', None),  #: {date}
            ('published_after', None),  #: {date}
            ('published_before', None),  #: {date}
            ('discovery_method', None),  #: {value}
            ('discovery_auth_types', None),  #: {value}
            ('show_pci_reasons', None),  #: {0|1}
        ]
        call = '/api/2.0/fo/knowledge_base/vuln/'
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # turn python Datetimes into qualys args...
        # TODO: implement date conversion.
        convert = (
            'last_modified_after',
            'last_modified_before',
            'last_modified_by_user_after',
            'last_modified_by_user_before',
            'last_modified_by_service_after',
            'last_modified_by_service_before',
            'published_after',
            'published_before',
        )
        for dparam in convert:
            if dparam in params:
                try:
                    if type(params[dparam]) != str:
                        params[dparam] = date_param_format(params[dparam])
                except:
                    logger.warn(
                        'strange date param %s - %s' % (dparam, params[dparam]))
        result = None
        if 'file' in kwargs:
            sourcefile = open(kwargs.pop('file'), 'rb')
            result = self.parseResponse(source=sourcefile,
                                        consumer_prototype=consumer_prototype,
                                        obj_elem_map={
                                            'VULN': QKBVuln,
                                            'WARNING': AssetWarning,
                                        })
            sourcefile.close()
        else:
            result = self.parseResponse(source=call, data=params,
                                        consumer_prototype=consumer_prototype,
                                        obj_elem_map={
                                            'VULN': QKBVuln,
                                            'WARNING': AssetWarning,
                                        })
        return result

    def listReportTemplates(self):
        '''Load a list of report templates'''
        call = 'report_template_list.php'
        return self.parseResponse(source=call, data=None)

    def listReports(self, *args, **kwargs):
        '''Executes a list of available reports.  Filtering parameters allowed
        (from the API Documentation):

        :parameter id:
            request info on a sepecific report id
        :parameter state:
            only include reports witha a given state (such as finished)
        :parameter user_login:
            only include reports launched by a specific user
        :parameter expires_before_datetime:
            A date/time by which any reports returned
            would have.  String format.  Qualys format.
            expired.
        :parameter filter:
            A dictionary used to filter the result set.  The result set can be
            filter on any property/value pair for a Report object
        .. seealso:: :class:`qualysapi.api_objects.Report`
        '''
        call = '/api/2.0/fo/report/'
        parameters = {
            'action': 'list',
        }
        for param in ('id', 'state', 'user_login', 'expires_before_datetime'):
            if param in kwargs:
                parameters[param] = kwargs[param]

        results = self.parseResponse(source=call, data=parameters)
        if 'filter' in kwargs:
            fdict = kwargs['filter']
            return filterObjects(kwargs['filter'], results)
        else:
            return results

    def notScannedSince(self, days):
        call = '/api/2.0/fo/asset/host/'
        parameters = {'action': 'list', 'details': 'All'}
        hostData = objectify.fromstring(self.request(call, data=parameters))
        hostArray = []
        today = datetime.date.today()
        for host in hostData.RESPONSE.HOST_LIST.HOST:
            last_scan = str(host.LAST_VULN_SCAN_DATETIME).split('T')[0]
            last_scan = qualys_datetime_to_python(last_scan)
            if (today - last_scan).days >= days:
                hostArray.append(Host(host.DNS, host.ID, host.IP,
                                      host.LAST_VULN_SCAN_DATETIME, host.NETBIOS, host.OS,
                                      host.TRACKING_METHOD))

        return hostArray

    def addIP(self, ips, vmpc):
        # 'ips' parameter accepts comma-separated list of IP addresses.
        # 'vmpc' parameter accepts 'vm', 'pc', or 'both'. (Vulnerability
        # Managment, Policy Compliance, or both)
        call = '/api/2.0/fo/asset/ip/'
        enablevm = 1
        enablepc = 0
        if vmpc == 'pc':
            enablevm = 0
            enablepc = 1
        elif vmpc == 'both':
            enablevm = 1
            enablepc = 1

        parameters = {'action': 'add', 'ips': ips, 'enable_vm': enablevm, 'enable_pc': enablepc}
        self.request(call, data=parameters)

    def asyncListMaps(self, bind=False):
        '''
        An asynchronous call to the parser/consumer framework to return a list
        of maps.
        '''
        raise QualyException('Not yet implemented')

    def listMaps(self, *args, **kwargs):
        '''
        Initially this is a api v1 only capability of listing available map
        reports.
        '''
        call = 'map_report_list.php'
        data = {}
        return self.parseResponse(source=call, data=data)

    def listScheduledScans(self, consumer_prototype=None, **kwargs):
        call = '/api/2.0/fo/schedule/scan/'
        optional_params = [
            ('action', 'list'),
            ('echo_request', '0'),
            ('id', None),
            ('active', None),
        ]
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        return self.parseResponse(source=call, data=params, http_method='get',
                                  consumer_prototype=consumer_prototype,
                                  obj_elem_map={
                                      'SCAN': Scan,
                                      'WARNING': AssetWarning,
                                  },
                                  **kwargs)

    def listScans(self, consumer_prototype=None, **kwargs):
        # 'launched_after' parameter accepts a date in the format: YYYY-MM-DD
        # 'state' parameter accepts "Running", "Paused", "Canceled", "Finished", "Error", "Queued", and "Loading".
        # 'title' parameter accepts a string
        # 'type' parameter accepts "On-Demand", and "Scheduled".
        # 'user_login' parameter accepts a user name (string)
        call = '/api/2.0/fo/scan/'
        optional_params = [
            ('action', 'list'),
            ('echo_request', '0'),
            ('scan_ref', None),
            ('show_id', None),
            ('state', None),
            ('processed', None),
            ('type', None),
            ('target', None),
            ('user_login', None),
            ('launched_after_datetime', None),
            ('launched_before_datetime', None),
            ('show_ags', None),
            ('show_op', '1'),
            ('show_status', None),
            ('show_last', None),
            ('pci_only', None),
            ('ignore_target', None),
        ]
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }

        return self.parseResponse(source=call, data=params,
                                  consumer_prototype=consumer_prototype,
                                  obj_elem_map={
                                      'SCAN': Scan,
                                      'WARNING': AssetWarning,
                                  },
                                  **kwargs)

    def launchScan(self, title, option_title, iscanner_name, asset_groups="",
                   ip=""):
        # TODO: Add ability to scan by tag.
        call = '/api/2.0/fo/scan/'
        parameters = {'action': 'launch', 'scan_title': title, 'option_title':
            option_title, 'iscanner_name': iscanner_name, 'ip': ip,
                      'asset_groups': asset_groups}
        if ip == "":
            parameters.pop("ip")

        if asset_groups == "":
            parameters.pop("asset_groups")

        scan_ref = objectify.fromstring(self.request(call,
                                                     data=parameters)).RESPONSE.ITEM_LIST.ITEM[1].VALUE

        call = '/api/2.0/fo/scan/'
        parameters = {'action': 'list', 'scan_ref': scan_ref, 'show_status': 1,
                      'show_ags': 1, 'show_op': 1}

        scan = objectify.fromstring(self.request(call,
                                                 data=parameters)).RESPONSE.SCAN_LIST.SCAN
        try:
            agList = []
            for ag in scan.ASSET_GROUP_TITLE_LIST.ASSET_GROUP_TITLE:
                agList.append(ag)
        except AttributeError:
            agList = []

        return Scan(agList, scan.DURATION, scan.LAUNCH_DATETIME,
                    scan.OPTION_PROFILE.TITLE, scan.PROCESSED, scan.REF,
                    scan.STATUS, scan.TARGET, scan.TITLE, scan.TYPE,
                    scan.USER_LOGIN)

    def getConnectionConfig(self):
        return self.conn.getConfig()

    def reportFromFile(self, filename, **kwargs):
        """reportFromFile
        load and parse a report from an xml file.

        :param filename:
        The name of the file to open and pass to the parser.
        :param kwargs:
        Passed through to other functions
        """
        with open(filename, 'rb') as source:
            # make sure we only return the report (since it's possible for the
            # result to be 'dirty'
            results = self.parseResponse(source=source, **kwargs)
            for item in results:
                if isinstance(item, Report):
                    return item

    def hostDetectionQuery(self, **kwargs):
        """hostDetectionQuery

        :param **kwargs: keyword arguments for api call
        """
        pass

    def assetGroupQuery(self, consumer_prototype=None, **kwargs):
        """assetGroupQuery

        Implements the list functions from the qualys Asset Group API.

        :Note: show_attributes=ALL can take a very long time

        """
        # p;ckle name/default pairs for kwargs
        optional_params = [
            ('action', 'list'),
            ('echo_request', '0'),
            ('ids', None),
            ('id_min', None),
            ('id_max', None),
            ('truncation_limit', None),  # default is 1000
            ('network_ids', None),
            ('unit_id', None),
            ('user_id', None),
            ('title', None),
            ('show_attributes', 'TITLE'),  # see docs for list
        ]
        call = '/api/2.0/fo/asset/group/'
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # return 1 or None.  API doesn't allow multiple.  Also make sure it's a
        # report and not a SimpleReturn (which can happen)
        results = self.parseResponse(source=call, data=params,
                                     consumer_prototype=consumer_prototype,
                                     obj_elem_map={
                                         'ASSET_GROUP_LIST': AssetGroupList,
                                         'WARNING': AssetWarning
                                     },
                                     **kwargs)
        if len(results) == 1:
            if isinstance(results[0], AssetGroupList):
                return results[0].asset_groups
        return results

    def addAssetGroup(self, title, **kwargs):
        optional_params = [
            ('action', 'add'),
            ('title', title),
            ('echo_request', '0'),
            ('network_id', None),
            ('ips', None),
        ]
        call = '/api/2.0/fo/asset/group/'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        return self.request(call, data=params)

    def editAssetGroup(self, id, **kwargs):
        optional_params = [
            ('action', 'edit'),
            ('id', id),
            ('title', None),
            ('echo_request', '0'),
            ('network_id', None),
            ('add_ips', None),
            ('remove_ips', None),
            ('set_ips', None),
        ]
        call = '/api/2.0/fo/asset/group/'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        return self.request(call, data=params)

    def deleteAssetGroup(self, id, **kwargs):
        optional_params = [
            ('action', 'delete'),
            ('id', id),
            ('echo_request', '0'),
        ]
        call = '/api/2.0/fo/asset/group/'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        return self.request(call, data=params)

    def hostListQuery(self, consumer_prototype=None, **kwargs):
        """hostListQuery

        :param consumer_prototype: Optional multiprocess consumer
        :param **kwargs: optional api parameters and keyword args
        """
        # p;ckle name/default pairs for kwargs
        optional_params = [
            ('action', 'list'),
            ('truncation_limit', None),  # default is 1000
            ('details', 'Basic'),  # see docs for list
            ('ips', None),
            ('ids', None),
            ('ag_ids', None),
            ('ag_titles', None),
            ('id_min', None),
            ('id_max', None),
            ('network_ids', None),
            ('no_vm_scan_since', None),  #: {date}
            ('no_compliance_scan_since', None),  #: {date}]
            ('vm_scan_since', None),  #: {date}
            ('compliance_scan_since', None),  #: {date}
            ('compliance_enabled', None),  #: {0|1}
            ('os_pattern', None),  #: {expression}
            ('use_tags', None),  #: {0|1}
            ('tag_set_by', None),  #: {id|name}
            ('tag_include_selector', None),  #: {any|all}
            ('tag_exclude_selector', None),  #: {any|all}
            ('tag_set_include', None),  #: {value}
            ('tag_set_exclude', None),  #: {value}
            ('show_tags', None),  #: {0|1}
            ('max_days_since_last_vm_scan', None),  #: {value}
            ('max_days_since_detection_updated', None),
        ]
        call = '/api/2.0/fo/asset/host/'
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # return 1 or None.  API doesn't allow multiple.  Also make sure it's a
        # report and not a SimpleReturn (which can happen)
        return self.parseResponse(source=call, data=params,
                                  consumer_prototype=consumer_prototype,
                                  obj_elem_map={
                                      'HOST': Host,
                                      'WARNING': AssetWarning,
                                  },
                                  **kwargs)

    def hostDetectionQuery(self, consumer_prototype=None, **kwargs):
        """hostListQuery

        :param consumer_prototype: Optional multiprocess consumer
        :param **kwargs: optional api parameters and keyword args
        """
        # p;ckle name/default pairs for kwargs
        optional_params = [
            ('action', 'list'),
            ('echo_request', '0'),  #: optional but default
            ('output_format', None),  #: {XML|CSV| CSV_NO_METADATA}
            ('truncation_limit', None),  # default is 1000
            ('ids', None),
            ('id_min', None),
            ('id_max', None),
            ('ips', None),
            ('ag_ids', None),
            ('ag_titles', None),
            ('network_ids', None),
            ('vm_scan_since', None),  #: {date}
            ('no_vm_scan_since', None),  #: {date}
            ('no_compliance_scan_since', None),  #: {date}]
            ('os_pattern', None),  #: {expression}
            ('active_kernels_only', None),  #: {0|1}
            ('truncation_limit', None),  #: {value}
            ('status', None),  #: {value} compliance_enabled={0|1}
            ('qids', None),  #: {value}
            ('severities', None),  #: {value}
            ('show_igs', None),  #: {0|1}
            ('include_search_list_titles', None),  #: {value}
            ('exclude_search_list_titles', None),  #: {value}
            ('include_search_list_ids', None),  #: {value,value...}
            ('exclude_search_list_ids', None),  #: {value,value...}
            ('use_tags', None),  #: {0|1} tag_set_by={id|name}
            ('tag_include_selector', None),  #: {any|all}
            ('tag_exclude_selector', None),  #: {any|all}
            ('tag_set_include', None),  #: {value}
            ('tag_set_exclude', None),  #: {value}
            ('show_tags', None),  #: {0|1}
            ('suppress_duplicated_data_from_csv', None),  #: {0|1}
            ('max_days_since_last_vm_scan', None),  #: {value}
            ('max_days_since_detection_updated', None),
        ]
        call = '/api/2.0/fo/asset/host/vm/detection/'
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # return 1 or None.  API doesn't allow multiple.  Also make sure it's a
        # report and not a SimpleReturn (which can happen)
        return self.parseResponse(source=call, data=params,
                                  consumer_prototype=consumer_prototype,
                                  obj_elem_map={
                                      'HOST': Host,
                                      'WARNING': AssetWarning,
                                  },
                                  **kwargs)

    def scannerApplianceQuery(self, consumer_prototype=None, **kwargs):
        """scannerApplianceQuery

                :param consumer_prototype: Optional multiprocess consumer
                :param **kwargs: optional api parameters and keyword args
                """
        # p;ckle name/default pairs for kwargs
        optional_params = [
            ('action', 'list'),
            ('echo_request', '0'),  #: optional but default
            ('output_mode', 'brief'),  #: {XML|CSV| CSV_NO_METADATA}
            ('scan_detail', None),  # default is 1000
            ('include_cloud_info', None),
            ('busy', None),
            ('scan_ref', None),
            ('name', None),
            ('ids', None),
            ('include_license_info', None),
        ]
        call = '/api/2.0/fo/appliance/'
        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # return 1 or None.  API doesn't allow multiple.  Also make sure it's a
        # report and not a SimpleReturn (which can happen)
        return self.parseResponse(source=call, data=params,
                                  consumer_prototype=consumer_prototype,
                                  obj_elem_map={
                                      'APPLIANCE': Appliance,
                                      'WARNING': AssetWarning,
                                  },
                                  **kwargs)

    def createScanner(self, name, **kwargs):
        optional_params = [
            ('action', 'create'),
            ('name', name),
            ('echo_request', '0'),
            ('polling_interval', None),
            ('asset_group_id', None),
        ]
        call = '/api/2.0/fo/appliance/'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # return self.request(call, data=params)
        return self.parseResponse(source=call, data=params,
                                  obj_elem_map={'APPLIANCE': ApplianceResponse, 'SIMPLE_RETURN': SimpleReturn})

    def deleteScanner(self, id, **kwargs):
        optional_params = [
            ('action', 'delete'),
            ('id', id),
        ]
        call = '/api/2.0/fo/appliance/'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        # return self.request(call, data=params)
        return self.parseResponse(source=call, data=params,
                                  obj_elem_map={'APPLIANCE': ApplianceResponse, 'SIMPLE_RETURN': SimpleReturn})

    def createConnector(self, connector_name, auth_id, tag_ids=[], region_codes=[]):
        # create XML
        service_request = etree.Element('ServiceRequest')
        data = etree.Element('data')
        service_request.append(data)
        connector = etree.Element('AwsAssetDataConnector')
        data.append(connector)
        name = etree.Element('name')
        name.text = connector_name
        connector.append(name)
        default_tags = etree.Element('defaultTags')
        connector.append(default_tags)
        set = etree.Element('set')
        default_tags.append(set)
        for tag_id in tag_ids:
            tag_simple = etree.Element('TagSimple')
            set.append(tag_simple)
            id = etree.Element('id')
            id.text = str(tag_id)
            tag_simple.append(id)
        activation = etree.Element('activation')
        connector.append(activation)
        activation_set = etree.Element('set')
        activation.append(activation_set)
        activation_module = etree.Element('ActivationModule')
        activation_module.text = 'VM'
        activation_set.append(activation_module)
        auth_record = etree.Element('authRecord')
        connector.append(auth_record)
        auth_id_tag = etree.Element('id')
        auth_id_tag.text = str(auth_id)
        auth_record.append(auth_id_tag)
        if not len(region_codes):
            all_regions = etree.Element('allRegions')
            all_regions.text = 'true'
            connector.append(all_regions)
        else:
            endpoints = etree.Element('endpoints')
            connector.append(endpoints)
            endpoint_set = etree.Element('set')
            endpoints.append(endpoint_set)
            for region in region_codes:
                aws_endpoint = etree.Element('AwsEndpointSimple')
                endpoint_set.append(aws_endpoint)
                region_code = etree.Element('regionCode')
                region_code.text = region
                aws_endpoint.append(region_code)

        call = '/create/am/awsassetdataconnector/'
        return self.request(call, data=etree.tostring(service_request))

    def searchTags(self, **kwargs):
        optional_params = [
            ('name', None),
            ('id', None),
            ('ruleType', None),
            ('parentTagId', None),
            ('color', None),
            ('name_operator', 'EQUALS'),
            ('id_operator', 'EQUALS'),
            ('ruleType_operator', 'EQUALS'),
            ('parentTagId_operator', 'EQUALS'),
            ('color_operator', 'EQUALS')
        ]
        call = '/search/am/tag/'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        service_request = etree.Element('ServiceRequest')
        filters = etree.Element('filters')
        service_request.append(filters)
        for field, value in params.items():
            if not '_operator' in field:
                criteria = etree.Element('Criteria')
                criteria.attrib['field'] = field
                criteria.attrib['operator'] = params['%s_operator' % field]
                criteria.text = value
                filters.append(criteria)
        xml = fromstring(self.request(call, data=etree.tostring(service_request)))
        return xmljson.parker.data(xml)

    def searchAWSAuth(self, **kwargs):
        optional_params = [
            ('name', None),
            ('id', None),
            ('description', None),
            ('created', None),
            ('modified', None),
            ('name_operator', 'EQUALS'),
            ('id_operator', 'EQUALS'),
            ('description_operator', 'EQUALS'),
            ('created_operator', 'EQUALS'),
            ('modified_operator', 'EQUALS')
        ]
        call = '/search/am/awsauthrecord'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        service_request = etree.Element('ServiceRequest')
        filters = etree.Element('filters')
        service_request.append(filters)
        for field, value in params.items():
            if not '_operator' in field:
                criteria = etree.Element('Criteria')
                criteria.attrib['field'] = field
                criteria.attrib['operator'] = params['%s_operator' % field]
                criteria.text = value
                filters.append(criteria)
        xml = fromstring(self.request(call, data=etree.tostring(service_request)))
        return xmljson.parker.data(xml)

    def deleteAWSAuth(self, **kwargs):
        optional_params = [
            ('name', None),
            ('id', None),
            ('description', None),
            ('created', None),
            ('modified', None),
            ('name_operator', 'EQUALS'),
            ('id_operator', 'EQUALS'),
            ('description_operator', 'EQUALS'),
            ('created_operator', 'EQUALS'),
            ('modified_operator', 'EQUALS')
        ]
        call = '/delete/am/awsauthrecord'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        service_request = etree.Element('ServiceRequest')
        filters = etree.Element('filters')
        service_request.append(filters)
        for field, value in params.items():
            if not '_operator' in field:
                criteria = etree.Element('Criteria')
                criteria.attrib['field'] = field
                criteria.attrib['operator'] = params['%s_operator' % field]
                criteria.text = value
                filters.append(criteria)
        xml = fromstring(self.request(call, data=etree.tostring(service_request)))
        return xmljson.parker.data(xml)

    def createAWSAuth(self, name, access_key, secret_key, **kwargs):
        optional_params = [
            ('name', name),
            ('accessKeyId', access_key),
            ('secretKey', secret_key),
            ('description', None),
        ]
        call = 'create/am/awsauthrecord'

        params = {
            key: kwargs.get(key, default) for (key, default) in
            optional_params if kwargs.get(key, default) is not None
        }
        service_request = etree.Element('ServiceRequest')
        data = etree.Element('data')
        service_request.append(data)
        aws_auth = etree.Element('AwsAuthRecord')
        data.append(aws_auth)
        for k,v in params.items():
            node = etree.Element(k)
            node.text = v
            aws_auth.append(node)
        xml = fromstring(self.request(call, data=etree.tostring(service_request)))
        return xmljson.parker.data(xml)

    def assetIterativeWrapper(self, consumer_prototype=None, max_results=0,
                              list_type_combine=None, exit=None, internal_call=None, **kwargs):
        """assetIterativeWrapper

        A common handler for the asset API to iterative many requests.  This
        can take significant time.

        :param consumer_prototype: Optional.  Result consumer.
        :param max_results: Optional.  Maximum number of results to return.
        :param list_type_combine: Optional.  Combine lists objects.
        :param exit: threading.Event or multiprocessing.Event.  Used to
        interrupt this function and return.
        :param internal_call: The internal funciton being iterated over.
        :param **kwargs: Arguments to internal_call.
        """
        if not internal_call:
            raise exceptions.QualysFrameworkException('Misuse of iterator.')
        if not exit:
            exit = threading.Event()
        # 1000 is the default so no need to pass on
        orig_truncation_limit = int(kwargs.get('truncation_limit', 1000))
        # ok so basically if there is a WARNING then check the CODE, parse the
        # URL and continue the loop.  Logging is preferred.
        id_min = kwargs.get('id_min', 1)
        itercount = 0
        while id_min and not exit.is_set():
            # reset each iteration
            truncation_limit = orig_truncation_limit
            itercount += 1
            if max_results and orig_truncation_limit * itercount > max_results:
                truncation_limit = max_results - (orig_truncation_limit * (itercount - 1))
                if truncation_limit <= 0:
                    id_min = None
                    continue
                else:
                    kwargs['truncation_limit'] = truncation_limit
            # update the id_min for this iteration
            kwargs['id_min'] = id_min
            # make sure blocking is disabled
            kwargs['block'] = False
            prev_result = internal_call(consumer_prototype, exit=exit, **kwargs)
            prev_id_min = id_min
            id_min = None
            for itm in reversed(prev_result):
                if isinstance(itm, AssetWarning):
                    id_min_tmp = itm.getQueryDict().get('id_min', None)
                    try:
                        id_min_tmp = int(id_min_tmp)
                        if id_min_tmp > prev_id_min:
                            id_min = id_min_tmp
                            logger.debug("ID_MIN: %s" % id_min)
                            break
                    except:
                        break
        return

    def iterativeHostDetectionQuery(self, **kwargs):
        """iterativehostDetectionQuery

        Feeds iteration off the WARNING element to pull all of the hosts in
        blocks.  This is, obviously, iterative.
        """
        return self.assetIterativeWrapper(
            internal_call=self.hostDetectionQuery, **kwargs)

    def iterativeHostListQuery(self, **kwargs):
        """iterativeHostListQuery

        Feeds iteration off the WARNING element to pull all of the hosts in
        blocks.  This is, obviously, iterative.
        """
        return self.assetIterativeWrapper(
            internal_call=self.hostListQuery, **kwargs)

    def iterativeAssetGroupQuery(self, **kwargs):
        """iterativeHostListQuery

        Feeds iteration off the WARNING element to pull all of the asset groups
        in blocks.  This is, obviously, iterative.
        """
        return self.assetIterativeWrapper(
            internal_call=self.assetGroupQuery, **kwargs)

    def finish(self):
        if self.import_buffer is not None:
            return self.import_buffer.finish(block=True)

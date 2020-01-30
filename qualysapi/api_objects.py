import datetime
import lxml
import logging

logger = logging.getLogger(__name__)
import pprint
import json
import types
from urllib import parse as urlparse
from multiprocessing import Process, Pool, Manager, get_context
from multiprocessing.queues import Queue

import threading
from qualysapi import exceptions
from qualysapi.util import qualys_datetime_to_python, unicode_str


# debugger
# import pudb


def jsonify(obj):
    if isinstance(obj, CacheableQualysObject):
        return obj.__dict__


def filterObjects(lfilter, tlist):
    '''
    utility function to filter a list of objects based on a dictionary-param
    union equality set.
    '''
    return list(result for result in tlist if not list(False for pn, cval in
                                                       lfilter.items() if getattr(result, pn, None) != cval))


def attrOnly(elem=None, attrname=None):
    """attrOnly
    returns an attribute as the value for an element rather than anything
    within the element.
    :param elem:
    the target element
    :param attrname:
    the target attribute
    """
    if elem is None or attrname is None:
        raise exceptions.QualysFrameworkException('attribute or element are'
                                                  'NoneType.')
    else:
        return elem.get(attrname, default=None)


class ObjTypeList(object):
    '''List of class of type helper for parser objects'''
    class_type = None
    xpath = None

    def __init__(self, ctype, xpath=None):
        self.class_type = ctype
        self.xpath = xpath

    def isXpath(self):
        return True if self.xpath else False


class WASCriteria(object):
    value = None

    def __init__(self, value):
        self.value = value
        if not value:
            raise exceptions.QualysFrameworkException('No field or value specified for WAS criteria')


class EQUALS(WASCriteria):
    operator = 'EQUALS'
    types = ['Integer', 'Text', 'Date', 'Keyword']

    def __init__(self, *args, **kwargs):
        super(EQUALS, self).__init__(*args, **kwargs)


class NOT_EQUALS(WASCriteria):
    operator = 'NOT EQUALS'
    types = ['Integer', 'Text', 'Date', 'Keyword']

    def __init__(self, *args, **kwargs):
        super(NOT_EQUALS, self).__init__(*args, **kwargs)


class GREATER(WASCriteria):
    operator = 'GREATER'
    types = ['Integer', 'Date']

    def __init__(self, *args, **kwargs):
        super(GREATER, self).__init__(*args, **kwargs)


class LESSER(WASCriteria):
    operator = 'LESSER'
    types = ['Integer', 'Date']

    def __init__(self, *args, **kwargs):
        super(LESSER, self).__init__(*args, **kwargs)


class IN(WASCriteria):
    operator = 'IN'
    types = ['Integer', 'Keyword']

    def __init__(self, *args, **kwargs):
        super(IN, self).__init__(*args, **kwargs)


class IS_EMPTY(WASCriteria):
    operator = 'IS EMPTY'
    types = ['Text']

    def __init__(self, *args, **kwargs):
        super(IS_EMPTY, self).__init__(*args, **kwargs)


class CacheableQualysObject(object):
    '''
    A base class implementing the api framework
    '''
    cdata = None

    def __init__(self, **kwargs):
        '''Superclass init function that handles json serializaiton'''
        if 'json' in kwargs:
            jsondict = json.loads(kwargs['json'])
            [setattr(self, key, jsondict[key]) for key in jsondict]

        if 'param_map' in kwargs:
            elem = kwargs.get('elem', None)
            if elem is None:
                try:
                    elem = lxml.objectify.fromstring(kwargs.get('xml'))
                except:
                    exmsg = 'param_map specified with no element or xml'
                    raise exceptions.QualysFrameworkException(exmsg)
            self.populateParameters(elem, kwargs.get('param_map'))
            # set any additional text value as self.cdata using itertext.
            try:
                self.cdata = lxml.etree.tostring(elem, method="text",
                                                 encoding='UTF-8')
            except:
                logger.warn(
                    'Failed text encode on field %s\n\t%r' % (elem.tag, self))

    def getKey(self):
        raise exceptions.QualysFrameworkException('You must implement this'
                                                  'function in yourself.')

    def __repr__(self):
        '''Represent y0'''
        try:
            return json.dumps(self.__dict__, default=jsonify)
        except:
            raise exceptions.QualysFrameworkException('jsonifying the class \
                failed!')

    def __eq__(self, other):
        '''Instance value equality (simple dict key/value comparison'''
        return self.__dict__ == other.__dict__

    def date_convert(self, attrname):
        """date_convert
        Convert a qualys datetime string attribute value into a python datetime
        obj.
        :param attrname:
        Attribute to convert
        """
        datestr = getattr(self, attrname, None)
        if datestr is not None and isinstance(datestr, str):
            datestr = str(datestr).replace('T', ' ').replace('Z',
                                                             '').split(' ')
            date = datestr[0].split('-')
            time = datestr[1].split(':')
            setattr(self, attrname, datetime.datetime(int(date[0]),
                                                      int(date[1]), int(date[2]), int(time[0]), int(time[1]),
                                                      int(time[2])))

    def populateParameters(self, elem, param_map):
        ''' This baseclass utility method allows easy mapping of parameters to
        tag names on elements.  This makes creating parsers easier for
        this particular API. '''
        # DEBUG
        #        #logger.debug(pprint.pformat(elem))
        #        #logger.debug(pprint.pformat(param_map))
        # TODO at some point make this a set/union funciton rather than
        # iterative
        # handle attributes
        # attrs are always string and always lower-case
        for (name, value) in elem.items():
            if name in param_map:
                (attrname, attrtype) = param_map[name]
                setattr(self, attrname, value)

        for child in elem.iterchildren(*(param_map.keys())):
            if child.tag not in param_map:
                continue
            (attrname, attrtype) = param_map[child.tag]
            if attrtype is str:
                setattr(self, attrname, ''.join(child.itertext()))
            elif attrtype is list:
                if getattr(self, attrname) is None:
                    setattr(self, attrname, [''.join(child.itertext())])
                else:
                    getattr(self, attrname).append(''.join(child.itertext()))
            elif attrtype is bool:
                text = ''.join(child.itertext())
                if text and int(text) == 0:
                    setattr(self, attrname, True)
                else:
                    setattr(self, attrname, False)
            elif attrtype is dict:
                self.populateParameters(child, attrname)
            elif type(attrtype) is types.FunctionType:
                setattr(self, attrname, attrtype(''.join(child.itertext())))
            elif type(attrtype) is ObjTypeList:
                if attrtype.isXpath():
                    if attrtype.class_type is str:
                        setattr(self, attrname,
                                [''.join(grandchild.itertext())
                                 for grandchild in
                                 child.xpath(attrtype.xpath)])
                    else:
                        setattr(self, attrname,
                                [attrtype.class_type(elem=grandchild)
                                 for grandchild in
                                 child.xpath(attrtype.xpath)])
                else:
                    if attrtype.class_type is str:
                        if getattr(self, attrname) is None:
                            setattr(self, attrname,
                                    [''.join(child.itertext())])
                        else:
                            getattr(self, attrname).append(
                                ''.join(child.itertext()))
                    else:
                        if getattr(self, attrname) is None:
                            setattr(self, attrname,
                                    [attrtype.class_type(elem=child)])
                        else:
                            getattr(self, attrname).append(
                                attrtype.class_type(elem=child))
            else:
                try:
                    setattr(self, attrname,
                            attrtype(elem=child, attrname=attrname))
                except:
                    logger.error('Unknown element handler type. %s' % attrtype)


class VulnInfo(CacheableQualysObject):
    '''
    A specific vulnerability.  Can be used in multiple reports and contexts.
    This class is used for both VULN_INFO and DETECTION
    ::
        <!ELEMENT VULN_INFO (QID, TYPE, PORT?, SERVICE?, FQDN?, PROTOCOL?, SSL?,
            INSTANCE?, RESULT?, FIRST_FOUND?, LAST_FOUND?, TIMES_FOUND?,
            VULN_STATUS?, CVSS_FINAL?, TICKET_NUMBER?, TICKET_STATE?)>

        <!ELEMENT QID (#PCDATA)>
        <!ATTLIST QID id IDREF #REQUIRED>

        <!ELEMENT TYPE (#PCDATA)>
        <!ELEMENT PORT (#PCDATA)>
        <!ELEMENT SERVICE (#PCDATA)>
        <!ELEMENT FQDN (#PCDATA)>
        <!ELEMENT PROTOCOL (#PCDATA)>
        <!ELEMENT SSL (#PCDATA)>

        <!ELEMENT RESULT (#PCDATA)>
        <!ATTLIST RESULT format CDATA #IMPLIED>

        <!ELEMENT FIRST_FOUND (#PCDATA)>
        <!ELEMENT LAST_FOUND (#PCDATA)>
        <!ELEMENT TIMES_FOUND (#PCDATA)>
        <!-- Note: VULN_STATUS is N/A for IGs -->
        <!ELEMENT VULN_STATUS (#PCDATA)>

        <!ELEMENT CVSS_FINAL (#PCDATA)>
        <!ELEMENT TICKET_NUMBER (#PCDATA)>
        <!ELEMENT TICKET_STATE (#PCDATA)>

        <!ELEMENT INSTANCE (#PCDATA)>
    '''
    cvss_final = None

    @property
    def first_seen(self):
        return self.first_found

    @first_seen.setter
    def first_seen(self, first_seen):
        self.first_found = first_seen

    first_found = None
    fqdn = None
    instance = None
    last_fixed_datetime = None

    @property
    def last_seen(self):
        return self.last_found

    @last_seen.setter
    def last_seen(self, last_seen):
        self.last_found = last_seen

    last_found = None
    last_test_datetime = None
    last_update_datetime = None
    port = None
    protocol = None
    qid = None
    result = None
    service = None
    severity = None
    ssl = None
    status = None
    ticket_number = None
    ticket_state = None
    times_found = None
    type = None
    active_kernel = None
    active_service = None
    active_config = None
    last_reopened = None
    times_reopened = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'QID': ('qid', unicode_str),
            'TYPE': ('type', unicode_str),
            'PORT': ('port', unicode_str),
            'SERVICE': ('service', unicode_str),
            'FQDN': ('fqdn', unicode_str),
            'PROTOCOL': ('protocol', unicode_str),
            'SSL': ('ssl', unicode_str),
            'AFFECT_RUNNING_KERNEL': ('active_kernel', unicode_str),
            'AFFECT_RUNNING_SERVICE': ('active_service', unicode_str),
            'AFFECT_EXPLOITABLE_CONFIG': ('active_config', unicode_str),
            # NOTE: I haven't implemented the format attribute because it is
            # always implied as 'table' with the data in RESULT being a
            # delim-text format of some kind (CSV/TSV/Columar Text) but the
            # attribute just says 'table'.  We will have to do discovery
            # at some point to expose parsing functionality for this data that
            # isn't XML based parsing.
            'RESULT': ('result', unicode_str),
            'RESULTS': ('result', unicode_str),
            'FIRST_FOUND': ('first_found',
                            qualys_datetime_to_python),
            'LAST_FOUND': ('last_found',
                           qualys_datetime_to_python),
            'FIRST_FOUND_DATETIME': ('first_found',
                                     qualys_datetime_to_python),
            'LAST_FOUND_DATETIME': ('last_found',
                                    qualys_datetime_to_python),
            'LAST_TEST_DATETIME': ('last_test_datetime',
                                   qualys_datetime_to_python),
            'LAST_UPDATE_DATETIME': ('last_update_datetime',
                                     qualys_datetime_to_python),
            'LAST_FIXED_DATETIME': ('last_fixed_datetime',
                                    qualys_datetime_to_python),
            'LAST_REOPENED_DATETIME': ('last_reopened',
                                       qualys_datetime_to_python),
            'TIMES_REOPENED': ('times_reopened', unicode_str),
            'TIMES_FOUND': ('times_found', unicode_str),
            'VULN_STATUS': ('status', unicode_str),
            'STATUS': ('status', unicode_str),
            'CVSS_FINAL': ('cvss_final', unicode_str),
            'TICKET_NUMBER': ('ticket_number', unicode_str),
            'TICKET_STATE': ('ticket_state', unicode_str),
            'INSTANCE': ('instance', unicode_str),
            'SEVERITY': ('severity', unicode_str),
        })
        super(VulnInfo, self).__init__(*args, **kwargs)
        # format the last scan into a dagtetime
        for datefield in ('first_found', 'last_found'):
            self.date_convert(datefield)


class UserDefs(CacheableQualysObject):
    """UserDefs
    Encapsulates user label/value pairs (max 3).  This object is iterable for
    ease of use.
    :Example:
    ::
        for (label,value) in Host.user_def:
            print(label,value)
    ::
        <!ELEMENT USER_DEF (LABEL_1?, LABEL_2?, LABEL_3?, VALUE_1?, VALUE_2?,
            VALUE_3?)>
        <!ELEMENT LABEL_1 (#PCDATA)>
        <!ELEMENT LABEL_2 (#PCDATA)>
        <!ELEMENT LABEL_3 (#PCDATA)>
        <!ELEMENT VALUE_1 (#PCDATA)>
        <!ELEMENT VALUE_2 (#PCDATA)>
        <!ELEMENT VALUE_3 (#PCDATA)>
    """
    label_1 = None
    label_2 = None
    label_3 = None
    value_1 = None
    value_2 = None
    value_3 = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'LABEL_1': ('label_1', unicode_str),
            'LABEL_2': ('label_2', unicode_str),
            'LABEL_3': ('label_3', unicode_str),
            'VALUE_1': ('value_1', unicode_str),
            'VALUE_2': ('value_2', unicode_str),
            'VALUE_3': ('value_3', unicode_str),
        })
        super(UserDefs, self).__init__(*args, **kwargs)

    def __iter__(self):
        for x in range(1, 4):
            yield (getattr(self, 'label_%d' % x),
                   getattr(self, 'value_%d' % x))


class IP(CacheableQualysObject):
    '''IP address along with metadata'''
    network_id = None  #: Attempts to cross-link NICs to hosts
    ipv6 = None  #: Flag for IPV6
    value = None  #: Can be IPV4 or IPV6.  Tricky.

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'network_id': ('network_id', unicode_str),
            'v6': ('ipv6', unicode_str),
        })
        super(IP, self).__init__(*args, **kwargs)
        elem = kwargs.get('elem', None)
        if elem is not None:
            self.value = ''.join(elem.itertext())

    def __str__(self):
        return self.value


class AssetGroupIdSet(CacheableQualysObject):
    '''Element group handling for IPSET tags. Network ID attributes are
    ignored.
    ::
        <!ATTLIST ID network_id CDATA #IMPLIED>
        <!ATTLIST ID_RANGE network_id CDATA #IMPLIED>
    '''
    ids = None  #: string list of ips
    id_ranges = None  #: string list of ip ranges

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'ID': ('ids', list),
            'ID_RANGE': ('id_ranges', list),
        })
        super(AssetGroupIdSet, self).__init__(*args, **kwargs)


class Host(CacheableQualysObject):
    '''
    Upgraded host information for reports

    This class implements the Host element for multiple Qualys DTDs
    ::
        <!-- host-vm-detection host --!>
        <!ELEMENT HOST (ID, IP?, IPV6?, TRACKING_METHOD?, NETWORK_ID?,
                OS?, OS_CPE?, DNS?, NETBIOS?, QG_HOSTID?,
                LAST_SCAN_DATETIME?, TAGS?, DETECTION_LIST)>
        <!-- Asset Data Report host --!>
        <!ELEMENT HOST (ERROR | (IP, TRACKING_METHOD, ASSET_TAGS?,
            DNS?, NETBIOS?, QG_HOSTID?, IP_INTERFACES?, OPERATING_SYSTEM?, OS_CPE?,
            ASSET_GROUPS?, VULN_INFO_LIST?))>

        <!ELEMENT IP (#PCDATA)>
        <!ATTLIST IP
          network_id  CDATA  #IMPLIED
          v6  CDATA  #IMPLIED
        >
        <!ELEMENT TRACKING_METHOD (#PCDATA)>

        <!-- asset handling diff -->
        <!ELEMENT ASSET_TAGS (ASSET_TAG+)>
        <!ELEMENT ASSET_TAG (#PCDATA)>
        <!-- vs -->
        <!ELEMENT TAGS (TAG+)>

        <!ELEMENT DNS (#PCDATA)>
        <!ELEMENT NETBIOS (#PCDATA)>
        <!ELEMENT QG_HOSTID (#PCDATA)>
        <!ELEMENT IP_INTERFACES (IP*)>
        <!ELEMENT OPERATING_SYSTEM (#PCDATA)>
        <!ELEMENT OS_CPE (#PCDATA)>
        <!ELEMENT ASSET_GROUPS (ASSET_GROUP_TITLE+)>
        <!ELEMENT VULN_INFO_LIST (VULN_INFO+)>

        <!-- Alternate Host Elements -->
        <!ELEMENT ID (#PCDATA)>
        <!ELEMENT IPV6 (#PCDATA)>
        <!ELEMENT NETWORK_ID (#PCDATA)>
        <!ELEMENT OS (#PCDATA)>
        <!ELEMENT LAST_SCAN_DATETIME (#PCDATA)>
        <!ELEMENT TAGS (TAG+)>
        <!ELEMENT DETECTION_LIST (DETECTION+)>

    Due to the fact that some DTDs contain identical information in
    different elements, this class converts different elements in different XML
    result sets into the same properties where relevant (and documented).

    Examples:
        DETECTION_LIST is roughly analagous to VULN_INFO_LIST although there
        are significant differences in available information.  To compensate
        for this, VulnInfo is used for both, but will have information
        available in the properties from a DL vuln than a VIL vuln.

    '''
    dns = None  #: FQDN if available
    id = None  #: Qualys Internal host ID
    id_set = None  #: a set of ids linking hosts together
    ip = None  #: :class:`IP` primary.interface
    last_scan = None  #: Last time scanned.
    netbios = None  #: NETBIOS if available
    os_cpe = None  #: Host OS Common Platform Enumeration
    tracking_method = None  #: The field used to identify host
    asset_tags = None  #: List of associated asset tags.
    tags = None  #: Alternate list of associated asset tags.
    interfaces = None  #: List of all detected interfaces
    asset_groups = None  #: List of associated asset groups.
    vulns = None  #: Known vulnerabilities.
    operating_system = None  #: host-reported OS
    asset_group_ids = None  #: CSV list of agids from Asset API Host List
    parent_stub = None
    comments = None
    ec2_instance_id = None
    ip_ranges = None
    last_compliance_scan_datetime = None
    last_vuln_scan_datetime = None
    network_id = None
    owner = None
    qg_hostid = None
    user_def = None

    @property
    def last_scan_datetime(self):
        return self.last_scan

    @last_scan_datetime.setter
    def last_scan_datetime(self, dtobj):
        self.last_scan = dtobj

    def __init__(self, *args, **kwargs):
        """__init__

        :param dns: depricated orderd argument.
        :param id: depricated orderd argument.
        :param ip: depricated orderd argument.
        :param last_scan: depricated orderd argument.
        :param netbios: depricated orderd argument.
        :param os: depricated orderd argument.
        :param tracking_method: depricated orderd argument.
        :param *args: allows all the above depricated ordered arguments.  kwargs
        specification is preferred.  Also allows the standard parent class xml
        or elem, but these can't be combined.  You must EITHER specify the
        xml/elem or the other ordered arguments until the old functionality has
        been removed.  len(args)>1 = depricated style.
        :param **kwargs: keyword params listsed above allowed as well as xmlobj, elem, xml and
        json (parent class prototype args).
        """
        # backwards compat
        if len(args) > 1:
            try:
                (self.dns, self.id, self.ip, self.last_scan, self.netbios,
                 self.operating_system, self.tracking_method) = args
            except:
                raise exceptions.QualysFrameworkException('You tried to pass'
                                                          'ordered arguments into this constructor.  Not only is'
                                                          'this depricated behavior, but you passed the wrong'
                                                          'arguments.')

        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'IP': ('ip', IP),
            'IP_ADDRESS': ('ip', IP),
            'IP_LIST': ('ip_ranges', ObjTypeList(IPRange,
                                                 xpath='RANGE')),
            'IPV6': ('ip', IP),  # overwrite ip
            'ID_SET': ('id_set', AssetGroupIdSet),
            'ID': ('id', unicode_str),
            'NETWORK_ID': ('network_id', unicode_str),
            'OWNER': ('owner', unicode_str),
            'COMMENTS': ('comments', unicode_str),
            'EC2_INSTANCE_ID': ('ec2_instance_id', unicode_str),
            'LAST_COMPLIANCE_SCAN_DATETIME': ('last_compliance_scan_datetime',
                                              qualys_datetime_to_python),
            'LAST_VULN_SCAN_DATETIME': ('last_scan_datetime',
                                        qualys_datetime_to_python),
            'LAST_SCAN_DATETIME': ('last_scan_datetime',
                                   qualys_datetime_to_python),
            'TRACKING_METHOD': ('tracking_method', unicode_str),
            'USER_DEF': ('user_def', UserDefs),
            'ASSET_TAGS': ('asset_tags', ObjTypeList(unicode_str,
                                                     xpath='ASSET_TAG')),
            'TAGS': ('asset_tags', ObjTypeList(AssetTag,
                                               xpath='TAG')),
            'DNS': ('dns', unicode_str),
            'NETBIOS': ('netbios', unicode_str),
            'QG_HOSTID': ('qg_hostid', unicode_str),
            'OPERATING_SYSTEM': ('operating_system', unicode_str),
            'OS': ('operating_system', unicode_str),
            'OS_CPE': ('os_cpe', unicode_str),
            'IP_INTERFACES': ('interfaces', ObjTypeList(IP,
                                                        xpath='IP')),
            'ASSET_GROUPS': ('asset_groups', ObjTypeList(unicode_str,
                                                         xpath='ASSET_GROUP_TITLE')),
            'ASSET_GROUP_IDS': ('asset_group_ids', unicode_str),
            'VULN_INFO_LIST': ('vulns', ObjTypeList(VulnInfo,
                                                    xpath='VULN_INFO')),
            'DETECTION_LIST': ('vulns', ObjTypeList(VulnInfo,
                                                    xpath='DETECTION')),
        })
        super(Host, self).__init__(*args, **kwargs)

        self.parent_stub = kwargs.get('report_stub', None)


class WASServiceResponse(CacheableQualysObject):
    responseCode = None
    count = None
    hasMoreRecords = None
    lastId = None
    data = None
    findings = None
    error = None

    class WebApp(CacheableQualysObject):
        id = None
        name = None
        url = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'id': ('id', unicode_str),
                    'name': ('name', unicode_str),
                    'url': ('url', unicode_str)
                })
                super(WASServiceResponse.WebApp, self).__init__(*args, **kwargs)

    class Finding(CacheableQualysObject):
        id = None
        qid = None
        name = None
        type = None
        findingType = None
        severity = None
        url = None
        status = None
        firstDetectedDate = None
        lastDetectedDate = None
        lastTestedDate = None
        timesDetected = None
        webApp = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'id': ('id', unicode_str),
                    'qid': ('qid', unicode_str),
                    'name': ('name', unicode_str),
                    'type': ('type', unicode_str),
                    'findingType': ('findingType', unicode_str),
                    'severity': ('severity', unicode_str),
                    'url': ('url', unicode_str),
                    'status': ('status', unicode_str),
                    'firstDetectedDate': ('firstDetectedDate', qualys_datetime_to_python),
                    'lastDetectedDate': ('lastDetectedDate', qualys_datetime_to_python),
                    'lastTestedDate': ('lastTestedDate', unicode_str),
                    'timesDetected': ('timesDetected', unicode_str),
                    'webApp': ('webApp', WASServiceResponse.WebApp)
                })
            super(WASServiceResponse.Finding, self).__init__(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'responseCode': ('responseCode', unicode_str),
            'count': ('count', unicode_str),
            'hasMoreRecords': ('hasMoreRecords', unicode_str),
            'lastId': ('lastId', unicode_str),
            #'data': ('findings', ObjTypeList(WASServiceResponse.Finding, xpath='Finding')),
            'responseErrorDetails': ('error', unicode_str)
        })

        super(WASServiceResponse, self).__init__(*args, **kwargs)


class InterfaceSettings(CacheableQualysObject):
    setting = None
    interface = None
    ip_address = None
    netmask = None
    gateway = None
    lease = None
    ipv6_address = None
    speed = None
    duplex = None
    dns = None

    class DNS(CacheableQualysObject):

        domain = None
        primary = None
        secondary = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'DOMAIN': ('domain', unicode_str),
                    'PRIMARY': ('primary', unicode_str),
                    'SECONDARY': ('secondary', unicode_str),
                })
            super(InterfaceSettings.DNS, self).__init__(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'SETTING': ('setting', unicode_str),
                'INTERFACE': ('interface', unicode_str),
                'IP_ADDRESS': ('ip_address', unicode_str),
                'NETMASK': ('netmask', unicode_str),
                'GATEWAY': ('gateway', unicode_str),
                'LEASE': ('lease', unicode_str),
                'IPV6_ADDRESS': ('ipv6_address', unicode_str),
                'SPEED': ('speed', unicode_str),
                'DUPLEX': ('duplex', unicode_str),
                'DNS': ('dns', self.DNS),
            })
        super(InterfaceSettings, self).__init__(*args, **kwargs)


class Proxy(CacheableQualysObject):
    protocal = None
    hostname = None
    port = None
    user = None

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'PROTOCAL': ('protocal', unicode_str),
                'HOSTNAME': ('hostname', unicode_str),
                'PORT': ('port', unicode_str),
                'USER': ('user', unicode_str),
            })
        super(Proxy, self).__init__(*args, **kwargs)


class CloudInfo(CacheableQualysObject):
    platform_provider = None
    ec2_info = None
    gce_info = None
    azure_info = None

    class EC2Info(CacheableQualysObject):

        instance_id = None
        instance_type = None
        kernel_id = None
        ami_id = None
        account_id = None
        instance_region = None
        instance_availability_zone = None
        instance_zone_type = None
        instance_vpc_id = None
        instance_subnet_id = None
        ip_address_private = None
        hostname_private = None
        security_groups = None
        api_proxy_settings = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'INSTANCE_ID': ('instance_id', unicode_str),
                    'INSTANCE_TYPE': ('instance_type', unicode_str),
                    'KERNEL_ID': ('kernel_id', unicode_str),
                    'AMI_ID': ('ami_id', unicode_str),
                    'ACCOUNT_ID': ('account_id', unicode_str),
                    'INSTANCE_REGION': ('instance_region', unicode_str),
                    'INSTANCE_AVAILABILITY_ZONE': ('instance_availability_zone', unicode_str),
                    'INSTANCE_ZONE_TYPE': ('instance_zone_type', unicode_str),
                    'INSTANCE_VPC_ID': ('instance_vpc_id', unicode_str),
                    'INSTANCE_SUBNET_ID': ('instance_subnet_id', unicode_str),
                    'IP_ADDRESS_PRIVATE': ('ip_address_private', unicode_str),
                    'HOSTNAME_PRIVATE': ('hostname_private', unicode_str),
                    'SECURITY_GROUPS': ('security_groups', unicode_str),
                    'API_PROXY_SETTINGS': ('api_proxy_settings', unicode_str),
                })
            super(CloudInfo.EC2Info, self).__init__(*args, **kwargs)

    class GCEInfo(CacheableQualysObject):

        instance_id = None
        machine_type = None
        project_id = None
        project_name = None
        preemptible = None
        instance_zone = None
        ip_address_private = None
        hostname_private = None
        ip_address_public = None
        instance_network = None
        gce_instance_tags = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'INSTANCE_ID': ('instance_id', unicode_str),
                    'MACHINE_TYPE': ('machine_type', unicode_str),
                    'PROJECT_ID': ('project_id', unicode_str),
                    'PROJECT_NAME': ('project_name', unicode_str),
                    'PREEMPTIBLE': ('preemptible', unicode_str),
                    'INSTANCE_ZONE': ('instance_zone', unicode_str),
                    'IP_ADDRESS_PRIVATE': ('ip_address_private', unicode_str),
                    'HOSTNAME_PRIVATE': ('hostname_private', unicode_str),
                    'IP_ADDRESS_PUBLIC': ('ip_address_public', unicode_str),
                    'INSTANCE_NETWORK': ('instance_network', unicode_str),
                    'GCE_INSTANCE_TAGS': ('gce_instance_tags', unicode_str),
                })
            super(CloudInfo.GCEInfo, self).__init__(*args, **kwargs)

    class AzureInfo(CacheableQualysObject):

        instance_id = None
        user_name = None
        instance_location = None
        deployment_mode = None
        ip_address_private = None
        hostname_private = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'INSTANCE_ID': ('instance_id', unicode_str),
                    'USERNAME': ('username', unicode_str),
                    'INSTANCE_LOCATION': ('instance_location', unicode_str),
                    'DEPLOYMENT_MODE': ('ami_id', unicode_str),
                    'IP_ADDRESS_PRIVATE': ('ip_address_private', unicode_str),
                    'HOSTNAME_PRIVATE': ('hostname_private', unicode_str),
                })
            super(CloudInfo.AzureInfo, self).__init__(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'PLATFORM_PROVIDER': ('platform_provider', unicode_str),
                'EC2_INFO': ('ec2_info', self.EC2Info),
                'GCE_INFO': ('gce_info', self.GCEInfo),
                'AZURE_INFO': ('azure_info', self.AzureInfo),
            })
        super(CloudInfo, self).__init__(*args, **kwargs)

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'INSTANCE_ID': ('instance_id', unicode_str),
                    'INSTANCE_TYPE': ('instance_type', unicode_str),
                    'KERNEL_ID': ('kernel_id', unicode_str),
                    'AMI_ID': ('ami_id', unicode_str),
                    'ACCOUNT_ID': ('account_id', unicode_str),
                    'INSTANCE_REGION': ('instance_region', unicode_str),
                    'INSTANCE_AVAILABILITY_ZONE': ('instance_availability_zone', unicode_str),
                    'INSTANCE_ZONE_TYPE': ('instance_zone_type', unicode_str),
                    'INSTANCE_VPC_ID': ('instance_vpc_id', unicode_str),
                    'INSTANCE_SUBNET_ID': ('instance_subnet_id', unicode_str),
                    'IP_ADDRESS_PRIVATE': ('ip_address_private', unicode_str),
                    'HOSTNAME_PRIVATE': ('hostname_private', unicode_str),
                    'SECURITY_GROUPS': ('security_groups', unicode_str),
                    'API_PROXY_SETTINGS': ('api_proxy_settings', unicode_str),
                })
            super(CloudInfo.EC2Info, self).__init__(*args, **kwargs)


class VLAN(CacheableQualysObject):
    id = None
    name = None
    ip_address = None
    netmask = None

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'ID': ('id', unicode_str),
                'NAME': ('name', unicode_str),
                'IP_ADDRESS': ('ip_address', unicode_str),
                'NETMASK': ('netmask', unicode_str),
            })
        super(VLAN, self).__init__(*args, **kwargs)


class Route(CacheableQualysObject):
    name = None
    ip_address = None
    netmask = None
    gateway = None

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'NAME': ('name', unicode_str),
                'IP_ADDRESS': ('ip_address', unicode_str),
                'NETMASK': ('netmask', unicode_str),
                'GATEWAY': ('gateway', unicode_str),
            })
        super(Route, self).__init__(*args, **kwargs)


class Appliance(CacheableQualysObject):
    id = None
    uuid = None
    name = None
    network_id = None
    software_version = None
    running_slices_count = None
    running_scan_count = None
    status = None
    cmd_only_start = None
    model_number = None
    serial_number = None
    activation_code = None
    interface_settings = None
    proxy_settings = None
    is_cloud_deployed = None
    cloud_info = None
    vlans = None
    static_routes = None
    ml_latest = None
    ml_version = None
    vulnsigs_latest = None
    vulnsigs_version = None
    asset_group_count = None
    asset_group_list = None
    asset_tags_list = None
    last_updated_date = None
    polling_interval = None
    heartbeats_missed = None
    ss_connection = None
    ss_last_connected = None
    fdcc_enabled = None
    user_list = None
    updated = None
    comments = None
    running_scans = None
    max_capacity_units = None
    license_info = None

    class LicenseInfo(CacheableQualysObject):

        qvsa_licenses_count = None
        qvsa_licenses_used = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'QVSA_LICENSES_COUNT': ('qvsa_licenses_count', unicode_str),
                    'QVSA_LICENSES_USED': ('qvsa_licenses_used', unicode_str),
                })
            super(Appliance.LicenseInfo, self).__init__(*args, **kwargs)

    class User(CacheableQualysObject):
        id = None
        name = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'ID': ('id', unicode_str),
                    'NAME': ('name', unicode_str),
                })
            super(Appliance.User, self).__init__(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'ID': ('id', unicode_str),
            'UUID': ('uuid', unicode_str),
            'NAME': ('name', unicode_str),
            'NETWORK_ID': ('network_id', unicode_str),
            'SOFTWARE_VERSION': ('software_version', unicode_str),
            'RUNNING_SLICES_COUNT': ('running_slices_count', unicode_str),
            'RUNNING_SCAN_COUNT': ('running_scan_count', unicode_str),
            'STATUS': ('status', unicode_str),
            'CMD_ONLY_START': ('cmd_only_start', unicode_str),
            'MODEL_NUMBER': ('model_number', unicode_str),
            'SERIAL_NUMBER': ('serial_number', unicode_str),
            'ACTIVATION_CODE': ('activation_code', unicode_str),
            'INTERFACE_SETTINGS': ('interface_settings', ObjTypeList(InterfaceSettings)),
            'PROXY_SETTINGS': ('proxy_settings', ObjTypeList(Proxy, xpath="PROXY")),
            'IS_CLOUD_DEPLOYED': ('is_cloud_deployed', unicode_str),
            'CLOUD_INFO': ('cloud_info', CloudInfo),
            'VLANS': ('vlans', ObjTypeList(VLAN, xpath="VLAN")),
            'STATIC_ROUTES': ('static_routes', ObjTypeList(Route, xpath="ROUTE")),
            'ML_LATEST': ('ml_latest', unicode_str),
            'ML_VERSION': ('ml_version', unicode_str),
            'VULNSIGS_LATEST': ('vulnsigs_latest', unicode_str),
            'VULNSIGS_VERSION': ('vulnsigs_version', unicode_str),
            'ASSET_GROUP_COUNT': ('asset_group_count', unicode_str),
            'ASSET_GROUP_LIST': ('asset_group_list', ObjTypeList(AssetGroup, xpath="ASSET_GROUP")),
            'ASSET_TAGS_LIST': ('asset_tags_list', ObjTypeList(AssetTag, xpath="ASSET_TAG")),
            'LAST_UPDATED_DATE': ('last_updated_date', qualys_datetime_to_python),
            'POLLING_INTERVAL': ('polling_interval', unicode_str),
            'HEARTBEATS_MISSED': ('heartbeats_missed', unicode_str),
            'SS_CONNECTION': ('ss_connection', unicode_str),
            'SS_LAST_CONNECTED': ('ss_last_connected', qualys_datetime_to_python),
            'FDCC_ENABLED': ('fdcc_enabled', unicode_str),
            'USER_LIST': ('user_list', ObjTypeList(self.User, xpath="USER_ACCOUNT")),
            'UPDATED': ('updated', unicode_str),
            'COMMENTS': ('comments', unicode_str),
            'RUNNING_SCANS': ('running_scans', ObjTypeList(Scan, xpath="SCAN")),
            'MAX_CAPACITY_UNITS': ('max_capacity_units', unicode_str),
            'LICENSE_INFO': ('license_info', self.LicenseInfo),
        })
        super(Appliance, self).__init__(*args, **kwargs)


class AssetGroupList(CacheableQualysObject):
    """AssetGroupList
    A set of AssetGroup objects specific to a document.  Useful for the
    glossary in an ASSET_GROUP_LIST_OUTPUT document or, abreviated within, a
    HOST_LIST_OUTPUT document.
    ::
        <!ELEMENT ASSET_GROUP_LIST (ASSET_GROUP+)>
    """
    asset_groups = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'ASSET_GROUP': ('asset_groups', ObjTypeList(AssetGroup)),
        })
        # #logger.debug(lxml.etree.tostring(kwargs.get('elem', None)))
        super(AssetGroupList, self).__init__(*args, **kwargs)

    def __iter__(self):
        return iter(self.asset_groups)

    def extend(self, items):
        if isinstance(items, type(self)):
            self.asset_groups.extend(items.asset_groups)
        else:
            self.asset_groups.extend(items)

    def append(self, item):
        if isinstance(item, type(self)):
            self.extend(item)
        else:
            self.asset_groups.append(item)

    def items(self):
        return self.asset_groups

    def size(self):
        return len(self.asset_groups)


# TODO: Add in API source signature identification ability.
class AssetGroup(CacheableQualysObject):
    """AssetGroup
    Class wrapper for ASSET_GROUP elements.  This can be from reports as well
    as the asset_group_list_output.dtd for the Asset Group API (v2) or even the
    v1 asset_group_list.php.

    This means that properties will be different between APIs used.  Please see
    the API documentation for which properties will be present.
    ::
        <!-- Asset group api DTD element definition -->
        <!ELEMENT ASSET_GROUP (ID, TITLE?,
            OWNER_USER_ID?, OWNER_UNIT_ID?, (NETWORK_ID|NETWORK_IDS)?,
            LAST_UPDATE?, BUSINESS_IMPACT?,
            CVSS_ENVIRO_CDP?, CVSS_ENVIRO_TD?, CVSS_ENVIRO_CR?,
            CVSS_ENVIRO_IR?, CVSS_ENVIRO_AR?,
            DEFAULT_APPLIANCE_ID?, APPLIANCE_IDS?,
            IP_SET?, DOMAIN_LIST?, DNS_LIST?, NETBIOS_LIST?,
            EC2_ID_LIST?, HOST_IDS?,
            ASSIGNED_USER_IDS?, ASSIGNED_UNIT_IDS?
        )>
        <!ELEMENT ID (#PCDATA)>
        <!ELEMENT TITLE (#PCDATA)>
        <!ELEMENT OWNER_USER_ID (#PCDATA)>
        <!ELEMENT OWNER_UNIT_ID (#PCDATA)>
        <!ELEMENT NETWORK_ID (#PCDATA)>
        <!ELEMENT NETWORK_IDS (#PCDATA)>
        <!ELEMENT LAST_UPDATE (#PCDATA)>
        <!ELEMENT BUSINESS_IMPACT (#PCDATA)>

        <!-- CVSS -->
        <!ELEMENT CVSS_ENVIRO_CDP (#PCDATA)>
        <!ELEMENT CVSS_ENVIRO_TD (#PCDATA)>
        <!ELEMENT CVSS_ENVIRO_CR (#PCDATA)>
        <!ELEMENT CVSS_ENVIRO_IR (#PCDATA)>
        <!ELEMENT CVSS_ENVIRO_AR (#PCDATA)>

        <!-- APPLIANCE_LIST -->
        <!ELEMENT DEFAULT_APPLIANCE_ID (#PCDATA)>
        <!ELEMENT APPLIANCE_IDS (#PCDATA)>

        <!-- IP_SET -->
        <!ELEMENT IP_SET ((IP|IP_RANGE)+)>
        <!ELEMENT IP (#PCDATA)>
        <!ATTLIST IP network_id CDATA #IMPLIED>
        <!ELEMENT IP_RANGE (#PCDATA)>
        <!ATTLIST IP_RANGE network_id CDATA #IMPLIED>

        <!-- DOMAIN_LIST -->
        <!ELEMENT DOMAIN_LIST (DOMAIN+)>
        <!ELEMENT DOMAIN (#PCDATA)>
        <!ATTLIST DOMAIN netblock CDATA "">
        <!ATTLIST DOMAIN network_id CDATA #IMPLIED>

        <!-- DNS_LIST -->
        <!ELEMENT DNS_LIST (DNS+)>
        <!ELEMENT DNS (#PCDATA)>
        <!ATTLIST DNS network_id CDATA "0">

        <!-- NETBIOS_LIST -->
        <!ELEMENT NETBIOS_LIST (NETBIOS+)>
        <!ELEMENT NETBIOS (#PCDATA)>
        <!ATTLIST NETBIOS network_id CDATA "0">

        <!-- EC2_IDS -->
        <!ELEMENT EC2_IDS (#PCDATA)>

        <!-- HOST_IDS -->
        <!ELEMENT HOST_IDS (#PCDATA)>

        <!-- USER_IDS -->
        <!ELEMENT ASSIGNED_USER_IDS (#PCDATA)>

        <!-- UNIT_IDS -->
        <!ELEMENT ASSIGNED_UNIT_IDS (#PCDATA)>
    """
    id = None
    title = None
    owner_user_id = None
    owner_unit_id = None
    network_id = None
    network_ids = None
    last_update = None
    business_impact = None
    cvss_enviro_cdp = None
    cvss_enviro_td = None
    cvss_enviro_cr = None
    cvss_enviro_ir = None
    cvss_enviro_ar = None
    default_appliance_id = None
    appliance_ids = None
    ip_set = None
    domain_list = None
    dns_list = None
    netbios_list = None
    ec2_ids = None
    host_ids = None
    assigned_user_ids = None
    assigned_unit_ids = None

    def __init__(self, *args, **kwargs):
        # backwards-compatible with old qualysapi (v1 api)
        if len(args) > 0:
            # business_impact
            self.business_impact = str(args[0])
            # id
            self.id = int(args[1])
            # last_update
            self.last_update = str(args[2])
            # scanips
            self.scanips = args[3]
            # scandns
            self.scandns = args[4]
            # scanner_appliances
            self.scanner_appliances = args[5]
            # title
            self.title = str(args[6])

        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'ID': ('id', unicode_str),
            'TITLE': ('title', unicode_str),
            'OWNER_USER_ID': ('owner_user_id', unicode_str),
            'OWNER_UNIT_ID': ('owner_unit_id', unicode_str),
            'NETWORK_ID': ('network_id', unicode_str),
            'NETWORK_IDS': ('network_ids', unicode_str),
            'LAST_UPDATE': ('last_update', unicode_str),
            'BUSINESS_IMPACT': ('business_impact', unicode_str),
            'CVSS_ENVIRO_CDP': ('cvss_enviro_cdp', unicode_str),
            'CVSS_ENVIRO_TD': ('cvss_enviro_td', unicode_str),
            'CVSS_ENVIRO_CR': ('cvss_enviro_cr', unicode_str),
            'CVSS_ENVIRO_IR': ('cvss_enviro_ir', unicode_str),
            'CVSS_ENVIRO_AR': ('cvss_enviro_ar', unicode_str),
            'DEFAULT_APPLIANCE_ID': ('default_appliance_id', unicode_str),
            'APPLIANCE_IDS': ('appliance_ids', unicode_str),
            'IP_SET': ('ip_set', IpSet),
            'DOMAIN_LIST': ('domain_list', ObjTypeList(unicode_str,
                                                       xpath="DOMAIN")),
            'DNS_LIST': ('dns_list', ObjTypeList(unicode_str,
                                                 xpath="DNS")),
            'NETBIOS_LIST': ('netbios_list', ObjTypeList(unicode_str,
                                                         xpath="NETBIOS")),
            'EC2_IDS': ('ec2_ids', unicode_str),
            'HOST_IDS': ('host_ids', unicode_str),
            'ASSIGNED_USER_IDS': ('assigned_user_ids', unicode_str),
            'ASSIGNED_UNIT_IDS': ('assigned_unit_ids', unicode_str),
            'ASSET_GROUP_TITLE': ('title', unicode_str),
            'RANGE': ('ranges', list),
        })
        super(AssetGroup, self).__init__(*args, **kwargs)

    class Domain(CacheableQualysObject):
        '''Name + Netblock range for a domain.
        ::
            <!ATTLIST DOMAIN netblock CDATA "">
            <!ATTLIST DOMAIN network_id CDATA #IMPLIED>
        '''
        netblock = None  #: string list of ips
        network_id = None  #: string list of ip ranges

        # aliased domain property from cdata
        @property
        def domain(self):
            return self.cdata

        @domain.setter
        def domain(self, value):
            self.cdata = value

        def __init__(self, *args, **kwargs):
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'netblock': ('netblock', unicode_str),
                'network_id': ('network_id', unicode_str),
            })
            super(AssetGroup.Domain, self).__init__(*args, **kwargs)

    def addAsset(conn, ip):
        call = '/api/2.0/fo/asset/group/'
        parameters = {'action': 'edit', 'id': self.id, 'add_ips': ip}
        conn.request(call, parameters)
        self.scanips.append(ip)

    def setAssets(conn, ips):
        call = '/api/2.0/fo/asset/group/'
        parameters = {'action': 'edit', 'id': self.id, 'set_ips': ips}
        conn.request(call, parameters)


# TODO validate and remove
# replaced
# class ReportTemplate(CacheableQualysObject):
#     def __init__(self, isGlobal, id, last_update, template_type, title, type, user):
#         self.isGlobal = int(isGlobal)
#         self.id = int(id)
#         self.last_update = str(last_update).replace('T', ' ').replace('Z', '').split(' ')
#         self.template_type = template_type
#         self.title = title
#         self.type = type
#         self.user = user.LOGIN

class IpSet(CacheableQualysObject):
    '''Element group handling for IPSET tags. Network ID attributes are
    ignored.
    ::
        <!ATTLIST IP network_id CDATA #IMPLIED>
        <!ATTLIST IP_RANGE network_id CDATA #IMPLIED>
    '''
    ips = None  #: string list of ips
    ip_ranges = None  #: string list of ip ranges

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'IP': ('ips', list),
            'IP_RANGE': ('ip_ranges', list),
        })
        super(IpSet, self).__init__(*args, **kwargs)


class Report(CacheableQualysObject):
    '''
    An object wrapper around qualys report handles.
    ::
       <!ELEMENT REPORT (ID, TITLE, TYPE, USER_LOGIN, LAUNCH_DATETIME, OUTPUT_FORMAT, SIZE, STATUS, EXPIRATION_DATETIME)>
           <!ELEMENT ID (#PCDATA)>
           <!ELEMENT TITLE (#PCDATA)>
           <!ELEMENT TYPE (#PCDATA)>
           <!ELEMENT USER_LOGIN (#PCDATA)>
           <!ELEMENT LAUNCH_DATETIME (#PCDATA)>
           <!ELEMENT OUTPUT_FORMAT (#PCDATA)>
           <!ELEMENT SIZE (#PCDATA)>
           <!ELEMENT STATUS (STATE, MESSAGE?, PERCENT?)>
           <!ELEMENT STATE (#PCDATA)>
           <!ELEMENT MESSAGE (#PCDATA)>
           <!ELEMENT PERCENT (#PCDATA)>
           <!ELEMENT EXPIRATION_DATETIME (#PCDATA)>
           <!ELEMENT EXPIRATION_DATETIME (#PCDATA)>
    Properties:

        .. note::
            previously used ordered arguments are depricated.  Right now the
            class is backwards compatible, but you cannot mix and match.  You have to
            use the previous named order or keyword arguments, not both.
        :property expiration_datetime: required expiration time of the report
        :property id: required id of the report
        :property launch_datetime: when the report was launched
        :property output_format: the output format of the report
        :property size: the size of the report file to download
        :property status: current qualys state of the report (scheduled, completed, paused, etc...)
        :property type: report type
        :property user_login: user who requested the report
    '''
    expiration_datetime = None  #: required expiration time of the report
    id = None  #: required id of the report
    launch_datetime = None  #: when the report was launched
    output_format = None  #: the output format of the report
    size = None  #: the size of the report file to download
    status = None  #: current qualys state of the report (scheduled, completed, paused, etc...)
    type = None  #: report type
    user_login = None  #: user who requested the report
    contents = None  #: the contents of a downloaded report
    title = None  #: The report title

    class ReportStatus(CacheableQualysObject):
        '''Encapsulate report status
        ::
           <!ELEMENT STATUS (STATE, MESSAGE?, PERCENT?)>
           <!ELEMENT STATE (#PCDATA)>
           <!ELEMENT MESSAGE (#PCDATA)>
           <!ELEMENT PERCENT (#PCDATA)>
        '''
        state = None
        message = None
        percent = None

        def __init__(self, *args, **kwargs):
            self.state = kwargs.pop('STATE', None)
            self.message = kwargs.pop('MESSAGE', None)
            self.percent = kwargs.pop('PERCENT', None)
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'STATE': ('state', unicode_str),
                'MESSAGE': ('message', unicode_str),
                'PERCENT': ('percent', unicode_str),
            })
            super(Report.ReportStatus, self).__init__(*args, **kwargs)

        def __eq__(self, other):
            '''Override default equality and check the state if it's a
            string.'''
            if isinstance(other, str):
                return self.state == other
            elif isinstance(other, Report.ReportStatus):
                return self.state == other.state

    def __init__(self, *args, **kwargs):
        # backwards-compatible ordered argument handling
        arg_order = [
            'expiration_datetime',
            'id',
            'launch_datetime',
            'output_format',
            'size',
            'status',
            'type',
            'user_login',
        ]
        # because of the old style handling where STATE is an etree element and
        # not a string the assumption must be handled before anyhting else...
        if len(args):
            [setattr(self, arg, args[n]) for (n, arg) in enumerate(n, arg_order)]
            # special handling for a single retarded attribute...
            if self.status is not None:
                self.status = status.STATE
        else:
            self.expiration_datetime = kwargs.pop('EXPIRATION_DATETIME', None)
            self.id = kwargs.pop('ID', None)
            self.launch_datetime = kwargs.pop('LAUNCH_DATETIME', None)
            self.output_format = kwargs.pop('OUTPUT_FORMAT', None)
            self.size = kwargs.pop('SIZE', None)
            self.status = kwargs.pop('STATUS', None)
            self.type = kwargs.pop('TYPE', None)
            self.user_login = kwargs.pop('USER_LOGIN', None)

        # default parent handler requirement...
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'TITLE': ('title', unicode_str),
            'EXPIRATION_DATETIME': ('expiration_datetime',
                                    qualys_datetime_to_python),
            'ID': ('id', unicode_str),
            'LAUNCH_DATETIME': ('launch_datetime',
                                qualys_datetime_to_python),
            'OUTPUT_FORMAT': ('output_format', unicode_str),
            'SIZE': ('size', unicode_str),
            'STATUS': ('status', self.ReportStatus),
            'TYPE': ('type', unicode_str),
            'USER_LOGIN': ('user_login', unicode_str)
        })
        super(Report, self).__init__(*args, **kwargs)

        # qualys naming work-around.  Turn a report stub into a real report
        rstub = kwargs.get('report_stub', None)
        if rstub is not None:
            self.expiration_datetime = rstub.expiration_datetime,
            self.id = rstub.id,
            self.launch_datetime = rstub.launch_datetime,
            self.output_format = rstub.output_format,
            self.size = rstub.size,
            self.status = rstub.status,
            self.type = rstub.type,
            self.user_login = rstub.user_login,

        # set keyword values, prefer over ordered argument values if both get
        # supplied
        # post attribute assignment processing
        # self.expiration_datetime = str(self.expiration_datetime).replace('T', ' ').replace('Z', '').split(' ')
        # self.launch_datetime = str(self.launch_datetime).replace('T', ' ').replace('Z', '').split(' ')
        # if id is a string change it to an int (used by other api objects)
        if isinstance(self.id, str):
            self.id = int(self.id)

    def add_contents(self, report_data):
        self.contents = report_data

    def haveContents(self):
        return True if self.contents else False


class CVSSImpact(CacheableQualysObject):
    '''
    CVSS impacted areas.
    '''
    confidentiality = None
    '''
    CONFIDENTIALITY child element.  A CVSS
    confidentiality impact metric. This metric measures the impact on
    confidentiality of a successfully exploited vulnerability. The
    value is: Undefined, None, Partial, or Complete. See “CVSS V2 Sub
    Metrics Mapping” below. (This element appears only when the CVSS
    Scoring feature is turned on in the user’s subscription and the API
    request includes the parameter details=All.)
    '''
    integrity = None
    '''
    INTEGRITY child element.  A CVSS integrity impact
    metric. This metric measures the impact to integrity of a
    successfully exploited vulnerability. The value is: Undefined,
    None, Partial, or Complete. See “CVSS V2 Sub Metrics Mapping”
    below. (This element appears only when the CVSS Scoring feature is
    turned on in the user’s subscription and the API request includes
    the parameter details=All.)
    '''
    availability = None
    '''
    AVAILABILITY child element.  A CVSS availability
    impact metric. This metric measures the impact to availability of a
    successfully exploited vulnerability. The value is: Undefined,
    None, Partial, or Complete. See “CVSS V2 Sub Metrics Mapping”
    below. (This element appears only when the CVSS Scoring feature is
    turned on in the user’s subscription and the API request includes
    the parameter details=All.)
    '''

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'CONFIDENTIALITY': ('confidentiality', unicode_str),
                'INTEGRITY': ('integrity', unicode_str),
                'AVAILABILITY': ('availability', unicode_str),
            })
        else:
            confidentiality = kwargs.pop('CONFIDENTIALITY', None)
            integrity = kwargs.pop('INTEGRITY', None)
            availability = kwargs.pop('AVAILABILITY', None)
        super(CVSSImpact, self).__init__(*args, **kwargs)


class CVSSAccess(CacheableQualysObject):
    '''
    A tuple of data, but made an object because of feature and
    extension desireability.

    .. autoclass:: CVSSAccess
        :members: vector, complexity
    '''
    vector = None
    '''
    :property vector:
        A CVSS access vector metric. This metric reflects how the
    vulnerability is exploited. The more remote an attacker can be to
    attack a host, the greater the vulnerability score. The value is
    one of the following: Network, Adjacent Network, Local Access, or
    Undefined. See “CVSS V2 Sub Metrics Mapping” below. (This element
    appears only when the CVSS Scoring feature is turned on in the
    user’s subscription and the API request includes the parameter
    details=All.)
    '''
    complexity = None
    '''
    :property complexity:
        A CVSS access complexity metric. This metric measures
    the complexity of the attack required to exploit the vulnerability
    once an attacker has gained access to the target system. The value
    is one of the following: Undefined, Low, Medium, or High. See “CVSS
    V2 Sub Metrics Mapping” below. (This element appears only when the
    CVSS Scoring feature is turned on in the user’s subscription and
    the API request includes the parameter details=All.)
    '''

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'VECTOR': ('vector', unicode_str),
                'COMPLEXITY': ('complexity', unicode_str),
            })
        else:
            vector = kwargs.pop('VECTOR', None)
            complexity = kwargs.pop('COMPLEXITY', None)
        super(CVSSAccess, self).__init__(*args, **kwargs)


class CVSS(CacheableQualysObject):
    '''
    CVSS metadata encoding wrapper object and helpers.
    ##CVSS element DTD:
    ::
        <!ELEMENT CVSS (BASE, TEMPORAL?, ACCESS?, IMPACT?,
            AUTHENTICATION?, EXPLOITABILITY?,
            REMEDIATION_LEVEL?, REPORT_CONFIDENCE?)>
            <!ELEMENT BASE (#PCDATA)>
            <!ATTLIST BASE source CDATA #IMPLIED>
            <!ELEMENT TEMPORAL (#PCDATA)>
            <!ELEMENT ACCESS (VECTOR?, COMPLEXITY?)>
            <!ELEMENT VECTOR (#PCDATA)>
            <!ELEMENT COMPLEXITY (#PCDATA)>
            <!ELEMENT IMPACT (CONFIDENTIALITY?, INTEGRITY?, AVAILABILITY?)>
            <!ELEMENT CONFIDENTIALITY (#PCDATA)>
            <!ELEMENT INTEGRITY (#PCDATA)>
            <!ELEMENT AVAILABILITY (#PCDATA)>
            <!ELEMENT AUTHENTICATION (#PCDATA)>
            <!ELEMENT EXPLOITABILITY (#PCDATA)>
            <!ELEMENT REMEDIATION_LEVEL (#PCDATA)>
            <!ELEMENT REPORT_CONFIDENCE (#PCDATA)>
    Parameters:

    :property base: BASE element.  CVSS base score.  (A CVSS base score assigned to the vulnerability. (This element appears only when the CVSS Scoring feature is turned on in the user’s subscription and the API request is for “Basic” details or “All” details.)
    :property temporal_score: TEMPORAL element.  A CVSS temporal score. (This element appears only when the CVSS Scoring feature is turned on in the user’s subscription and the API request is for “Basic” details or “All” details.)
    :property access: :class:`CVSSAccess` instance or None.
    :property impact: :class:`CVSSImpact` or None.
    :property authentication: AUTHENTICATION child element.  A CVSS authentication metric. This metric measures the number of times an attacker must authenticate to a target in order to exploit a vulnerability. The value is: Undefined, Non required, Require single instance, or Require multiple instances. See “CVSS V2 Sub Metrics Mapping” below. (This element appears only when the CVSS Scoring feature is turned on in the user’s subscription and the API request includes the parameter details=All.)
    :property exploitability: EXPLOITABILITY child element.  A CVSS exploitability metric. This metric measures the current state of        exploit techniques or code availability. The value is: Undefined, Unproven, Proof-of- concept, Functional, or Widespread. See “CVSS V2 Sub Metrics Mapping” below. (This element appears only when the CVSS Scoring feature is turned on in the user’s subscription and the API request includes the parameter details=All.)
    :property remediation_level: REMEDIATION_LEVEL child element.  A CVSS remediation level metric. The remediation level of a vulnerability is an important factor for prioritization. The value is: Undefined, Official-fix, Temporary-fix, Workaround, or Unavailable. See “CVSS V2 Sub Metrics Mapping” below. (This element appears only when the CVSS Scoring feature is turned on in the user’s subscription and the API request includes the parameter details=All.)
    :property report_confidence: REPORT_CONFIDENCE child element.  A CVSS report confidence metric. This metric measures the degree of confidence in the existence of the vulnerability and the credibility of the known technical details. The value is: Undefined, Not confirmed, Uncorroborated, or Confirmed. See “CVSS V2 Sub Metrics Mapping” below. (This element appears only when the CVSS Scoring feature is turned on in the user’s subscription and the API request includes the parameter details=All.)
    '''

    base = None
    temporal = None
    access = None
    impact = None
    authentication = None
    exploitability = None
    remediation_level = None
    report_confidence = None
    product = None
    vendor_id = None

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'BASE': ('base', unicode_str),
                'TEMPORAL': ('temporal', unicode_str),
                'ACCESS': ('access', CVSSAccess),
                'IMPACT': ('impact', CVSSImpact),
                'AUTHENTICATION': ('authentication', unicode_str),
                'EXPLOITABILITY': ('exploitability', unicode_str),
                'REMEDIATION_LEVEL': ('remediation_level', unicode_str),
                'REPORT_CONFIDENCE': ('report_confidence', unicode_str),
            })
        else:
            self.base = kwargs.pop('BASE', None)
            self.temporal = kwargs.pop('TEMPORAL', None)
            self.access = \
                CVSSAccess(**(kwargs.pop('ACCESS', {})))
            self.impact = \
                CVSSImpact(**(kwargs.pop('IMPACT', {})))
            self.authentication = kwargs.pop('AUTHENTICATION', None)
            self.exploitability = kwargs.pop('EXPLOITABILITY', None)
            self.remediation_level = kwargs.pop('REMEDIATION_LEVEL', None)
            self.report_confidence = kwargs.pop('REPORT_CONFIDENCE', None)
        super(CVSS, self).__init__(*args, **kwargs)


class CVE(CacheableQualysObject):
    '''
    CVE metadata encoding wrapper object and helpers.
    '''
    cve_id = None
    url = None

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'ID': ('cve_id', unicode_str),
                'URL': ('url', unicode_str),
            })
        else:
            self.cve_id = kwargs.pop('ID', None)
            self.url = kwargs.pop('URL', None)
        super(CVE, self).__init__(*args, **kwargs)


class QKBVuln(CacheableQualysObject):
    '''
    A class respresentation of a Qualys Knowledge Base entry.
    Properties:
        :property qid: the qualys id
        :property vtype: the qualys vuln type identifier
        :property severity: the qualys severity
        :property title: a human readable title-length description of the vulnerability
        :property vcat: a qualys-specific category for the vulnerability
        :property usermod_date: the most recent date that this vuln was modified by the auth account manager
        :property servicemod_date: the most recent date that this vuln was modified by the service
        :property publ_date: the date that this vuln was published
        :property bugtraq_listing: mozilla bugtraq information. A list of Bugtraq objects
        :property patch_avail: Boolean conversion of QKB 0/1 value.  Indicates a known patch is available.
        :property diagnosis: The Qualys service-provided evalution.
        :property diagnosis_notes: Admin/user account diagnosis recommendation notes.
        :property consequence: Service provided projected exploit fallout description.
        :property consequence_notes: Admin/user account notes on consequences.
        :property solution: Qualys/Service recommended remediation.
        :property solution_notes: Admin/user solution notes.
        :property pci_mustfix: PCI compliance fix mandated (boolean)
        :property pci_reasons: optional depending on query argument to provide pci pass/fail reasons. a list of PCIReason objects.
        :property cvss: a CVSS object. :class:`CVSS`
        :property affected_software: An ordered list (KQB ordering) of specific affected software (:class:`VulnSoftware` instances)
        :property assoc_vendors: An unordered dictionary of software vendors associated with any software associated with this vulnerability.  The dictionary is key=vendor_id, value=VulnVendor (see :class:`VulnVendor` )
        :property compliance_notice_list: A service-provided list of SLA/Standards that are or may be affected by this vulnerability.  Ordered list of Compliance objects, ordered as sent from qualys.
        :property known_exploits: a list of correlated known exploits (Exploit obj)
        :property known_malware: a list of known malware using exploits (Malware obj)
        :property remote_detectable: boolean
        :property auth_type_list: a list of auth types that can be used to detect vulnerability.  Strings.
    '''
    qid = None
    vtype = None
    severity = None
    title = None
    vcat = None
    usermod_date = None
    servicemod_date = None
    publ_date = None
    patch_avail = False
    diagnosis = None
    diagnosis_notes = None
    consequence = None
    consequence_notes = None
    solution = None
    solution_notes = None
    pci_mustfix = False
    cvss = None
    remote_detectable = False
    # lists
    bugtraq_listing = []
    cve_list = []
    pci_reasons = []
    affected_software = []
    vendor_list = []
    compliance_notice_list = []
    known_exploits = []
    known_malware = []
    auth_type_list = []

    class PCIReason(CacheableQualysObject):
        '''
        Class to hold information for PCI compliance failure associated with a
        vulnerability.
        '''
        pass

    class VulnSoftware(CacheableQualysObject):
        '''
        Information on known associated software.
        '''
        product = None
        vendor_id = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'PRODUCT': ('product', unicode_str),
                    'VENDOR': ('vendor_id', unicode_str),
                })
            else:
                self.product = kwargs.pop('PRODUCT', None)
                self.vendor_id = kwargs.pop('VENDOR', None)
            super(QKBVuln.VulnSoftware, self).__init__(*args, **kwargs)

    class VulnVendor(CacheableQualysObject):
        '''
        Information on vendors associated with software.
        '''
        vendor_id = None
        url = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'ID': ('vendor_id', unicode_str),
                    'URL': ('url', unicode_str),
                })
            else:
                self.vendor_id = kwargs.pop('ID', None)
                self.url = kwargs.pop('URL', None)
            super(QKBVuln.VulnVendor, self).__init__(*args, **kwargs)

    class Compliance(CacheableQualysObject):
        '''
        Information about a specific associated compliance failure association
        with a vulnerability.
        '''
        # TYPE, SECTION, DESCRIPTION
        ctype = None
        csection = None
        description = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'TYPE': ('ctype', unicode_str),
                    'SECTION': ('csection', unicode_str),
                    'DESCRIPTION': ('description', unicode_str),
                })
            else:
                self.ctype = kwargs.pop('TYPE', None)
                self.csection = kwargs.pop('SECTION', None)
                self.description = kwargs.pop('DESCRIPTION', None)
            super(QKBVuln.Compliance, self).__init__(*args, **kwargs)

    class Exploit(CacheableQualysObject):
        '''
        Information about a specific exploit associated with a vulnerability.
        '''
        src = None
        ref = None
        desc = None
        link = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'REF': ('ref', unicode_str),
                    'DESC': ('desc', unicode_str),
                    'LINK': ('link', unicode_str),
                })
            else:
                self.ref = kwargs.pop('REF', None)
                self.desc = kwargs.pop('DESC', None)
                self.link = kwargs.pop('LINK', None)
            super(QKBVuln.Exploit, self).__init__(*args, **kwargs)

    class Malware(CacheableQualysObject):
        '''
        Information about a specific piece of malware using a known exploit
        associated with this vulnerability.
        '''
        mwid = None
        mwtype = None
        platform = None
        alias = None
        rating = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'MW_ID': ('mwid', unicode_str),
                    'MW_TYPE': ('mwtype', unicode_str),
                    'MW_PLATFORM': ('platform', unicode_str),
                    'MW_ALIAS': ('alias', unicode_str),
                    'MW_RATING': ('rating', unicode_str),
                })
            else:
                self.mwid = kwargs.pop('MW_ID', None)
                self.mwtype = kwargs.pop('MW_TYPE', None)
                self.platform = kwargs.pop('MW_PLATFORM', None)
                self.alias = kwargs.pop('MW_ALIAS', None)
                self.rating = kwargs.pop('MW_RATING', None)
            super(QKBVuln.Malware, self).__init__(*args, **kwargs)

    class Bugtraq(CacheableQualysObject):
        '''
        A single bugtraq metadata set
        '''
        bugid = None
        url = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'ID': ('bugid', unicode_str),
                    'URL': ('url', unicode_str),
                })
            else:
                self.bugid = kwargs.pop('ID', None)
                self.url = kwargs.pop('URL', None)
            super(QKBVuln.Bugtraq, self).__init__(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        '''gracefully handle xml passed in as a blind ordered argument binary
        string.

        Otherwise operate with dictionaries/keyword arguments.
        '''
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'QID': ('qid', unicode_str),
                'VULN_TYPE': ('vtype', unicode_str),
                'SEVERITY_LEVEL': ('severity', unicode_str),
                'TITLE': ('title', unicode_str),
                'CATEGORY': ('vcat', unicode_str),
                'LAST_CUSTOMIZATION': ('usermod_date',
                                       qualys_datetime_to_python),
                'LAST_SERVICE_MODIFICATION_DATETIME': ('servicemod_date',
                                                       qualys_datetime_to_python),
                'PUBLISHED_DATETIME': ('publ_date',
                                       qualys_datetime_to_python),
                'PATCHABLE': ('patch_avail', unicode_str),
                'DIAGNOSIS': ('diagnosis', unicode_str),
                'DIAGNOSIS_COMMENT': ('diagnosis_notes', unicode_str),
                'CONSEQUENCE': ('consequence', unicode_str),
                'CONSEQUENCE_COMMENT': ('consequence_notes', unicode_str),
                'SOLUTION': ('solution', unicode_str),
                'SOLUTION_COMMENT': ('solution_notes', unicode_str),
                'PATCHABLE': ('patch_avail', bool),
                'PCI_FLAG': ('pci_mustfix', bool),
                'CVSS': ('cvss', CVSS),
                'BUGTRAQ_LIST': ('bugtraq_listing',
                                 ObjTypeList(self.Bugtraq)),
                'CVE_LIST': ('cve_list',
                             ObjTypeList(CVE, xpath='CVE')),
                'PCI_REASONS': ('pci_reasons',
                                ObjTypeList(self.PCIReason)),
                'SOFTWARE_LIST': ('affected_software',
                                  ObjTypeList(self.VulnSoftware)),
                'VENDOR_REFERENCE_LIST': ('vendor_list',
                                          ObjTypeList(self.VulnVendor)),
                'COMPLIANCE_LIST': ('compliance_notice_list',
                                    ObjTypeList(self.Compliance)),
                'CORRELATION': ('known_exploits',
                                ObjTypeList(self.Exploit, xpath='EXPLOITS/EXPLOIT')),
                'DISCOVERY': ({
                                  'remote_detectable': ('REMOTE', bool),
                                  'auth_type_list': ('AUTH_TYPE_LIST', list)}, dict)
            })
        else:
            # we assume standard kwarg arguments
            self.qid = kwargs.pop('QID', None)
            self.vtype = kwargs.pop('VULN_TYPE', None)
            self.severity = kwargs.pop('SEVERITY_LEVEL', None)
            self.title = kwargs.pop('TITLE', None)
            self.vcat = kwargs.pop('CATEGORY', None)
            self.usermod_date = kwargs.pop('LAST_CUSTOMIZATION', None)
            self.servicemod_date = kwargs.pop('LAST_SERVICE_MODIFICATION_DATETIME', None)
            self.publ_date = kwargs.pop('PUBLISHED_DATETIME', None)
            self.patch_avail = \
                False if int(kwargs.pop('PATCHABLE', 0)) else True
            self.diagnosis = kwargs.pop('DIAGNOSIS', None)
            self.diagnosis_notes = kwargs.pop('DIAGNOSIS_COMMENT', None)
            self.consequence = kwargs.pop('CONSEQUENCE', None)
            self.consequence_notes = kwargs.pop('CONSEQUENCE_COMMENT', None)
            self.solution = kwargs.pop('SOLUTION', None)
            self.solution_notes = kwargs.pop('SOLUTION_COMMENT', None)
            self.pci_mustfix = \
                False if int(kwargs.pop('PCI_FLAG', 0)) else True
            self.cvss = CVSS(elem=kwargs.pop('CVSS', None))
            # lists / subparse objects
            # TODO: make this graceful
            raise exceptions.QualysFrameworkException('Not yet implemented: \
                kwargs lists grace.')
        super(QKBVuln, self).__init__(*args, **kwargs)


class OptionProfile(CacheableQualysObject):
    title = None
    is_default = None

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'TITLE': ('title', unicode_str),
                'IS_DEFAULT': ('is_default', unicode_str),
            })
        else:
            self.title = kwargs.pop('TITLE', None)
            self.is_default = kwargs.pop('IS_DEFAULT', None)
        super(OptionProfile, self).__init__(*args, **kwargs)

        def __repr__(self):
            return self.title


class Map(CacheableQualysObject):
    '''
    A simple object wrapper around the qualys api concept of a map.

    Params:
    name = None
    ref = None
    date = None
    domain = None
    status = None
    report_id = None
    '''
    name = None
    ref = None
    date = None
    domain = None
    status = None
    report_id = None
    option_profiles = None

    def __init__(self, *args, **kwargs):
        '''Instantiate a new Map.'''
        # double-check the name?
        # self.name="".join(child.itertext())
        self.name = kwargs.pop('NAME', None)
        self.ref = kwargs.pop('ref', None)
        self.date = kwargs.pop('date', None)
        self.domain = kwargs.pop('domain', None)
        self.status = kwargs.pop('status', None)
        self.report_id = kwargs.pop('REPORT_ID', None)
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'NAME': ('name', unicode_str),
            'ref': ('ref', unicode_str),
            'date': ('date', unicode_str),
            'domain': ('domain', unicode_str),
            'status': ('status', unicode_str),
            'OPTION_PROFILE': ('option_profiles', ObjTypeList(OptionProfile)),
        })
        super(Map, self).__init__(*args, **kwargs)

        # superclass handles json serialized properties
        # but limited by subclasses.
        # TODO: this can be fixed now due to the type mapping function in the
        # superclass.  Fix it.
        if 'json' in kwargs:
            # our option profiles will be dicts... resolve
            self.option_profiles = [OptionProfile(json=json.dumps(op)) for op in
                                    self.option_profiles]
            # instantiate from an etree element
            # we are being initialized with an lxml element, assume it's in CVE export format

    def getKey(self):
        return self.ref if self.ref is not None else self.name

    def hasReport(self):
        return self.report_id is not None

    def setReport(self, **kwargs):
        report_id = kwargs.get('report_id', None)
        report = kwargs.get('report', None)
        if report_id is None and report is None:
            raise exceptions.QualysException('No report or report id.')
        self.report_id = report_id if report is None else report.id

    def __str__(self):
        '''Stringify this object.  NOT the same as repr().'''
        return '<Map name=\'%s\' date=\'%s\' ref=\'%s\' />' % (self.name, \
                                                               self.date, self.ref)


class MapResult(Map):
    '''The actual results of a map.'''

    def __init__(self):
        '''A map result is a subclass of Map but it gets it's values of name,
        ref, date, domain, status from different fields in a result.'''
        raise QualysException('This class hasn\'t been implemented yet.')


class Scan(CacheableQualysObject):
    id = None
    ref = None
    type = None
    title = None
    user_login = None
    launch_datetime = None
    duration = None
    processing_priority = None
    processed = None
    status = None
    target = None
    option_profile = None
    state = None

    class Status(CacheableQualysObject):
        '''
        A single bugtraq metadata set
        '''
        state = None

        def __init__(self, *args, **kwargs):
            if 'elem' in kwargs or 'xml' in kwargs:
                param_map = {}
                if 'param_map' in kwargs:
                    param_map = kwargs.pop('param_map', {})
                kwargs['param_map'] = param_map
                kwargs['param_map'].update({
                    'STATE': ('state', unicode_str),
                })
            else:
                self.bugid = kwargs.pop('STATE', None)
            super(Scan.Status, self).__init__(*args, **kwargs)

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'ID': ('id', unicode_str),
                'REF': ('ref', unicode_str),
                'TYPE': ('type', unicode_str),
                'TITLE': ('title', unicode_str),
                'USER_LOGIN': ('user_login', unicode_str),
                'LAUNCH_DATETIME': ('launch_datetime', qualys_datetime_to_python),
                'DURATION': ('duration', unicode_str),
                'PROCESSING_PRIORITY': ('processing_priority', unicode_str),
                'PROCESSED': ('processed', unicode_str),
                'STATUS': ('status', self.Status),
                'TARGET': ('target', unicode_str),
                'OPTION_PROFILE': ('option_profile', OptionProfile),
                'STATE': ('state', unicode_str),
            })
        super(Scan, self).__init__(*args, **kwargs)

    def __repr__(self):
        ''' Represent this object in a human-readable string '''
        return '''
    Scan '%s':
        lanch datetime: %s
        option profile: %s
        scan ref: %s
        status: %s
        target: %s
        type: %s
        user: %s
        ''' % (self.title, self.launch_datetime, self.option_profile, self.ref,
               self.status, self.target, self.type, self.user_login)

    def cancel(self, conn):
        cancelled_statuses = ['Cancelled', 'Finished', 'Error']
        if any(self.status in s for s in cancelled_statuses):
            raise ValueError("Scan cannot be cancelled because its status is " + self.status)
        else:
            call = '/api/2.0/fo/scan/'
            parameters = {'action': 'cancel', 'scan_ref': self.ref}
            conn.request(call, parameters)

            parameters = {'action': 'list', 'scan_ref': self.ref, 'show_status': 1}
            self.status = lxml.objectify.fromstring(conn.request(call, parameters)).RESPONSE.SCAN_LIST.SCAN.STATUS.STATE

    def pause(self, conn):
        if self.status != "Running":
            raise ValueError("Scan cannot be paused because its status is " + self.status)
        else:
            call = '/api/2.0/fo/scan/'
            parameters = {'action': 'pause', 'scan_ref': self.ref}
            conn.request(call, parameters)

            parameters = {'action': 'list', 'scan_ref': self.ref, 'show_status': 1}
            self.status = lxml.objectify.fromstring(conn.request(call, parameters)).RESPONSE.SCAN_LIST.SCAN.STATUS.STATE

    def resume(self, conn):
        if self.status != "Paused":
            raise ValueError("Scan cannot be resumed because its status is " + self.status)
        else:
            call = '/api/2.0/fo/scan/'
            parameters = {'action': 'resume', 'scan_ref': self.ref}
            conn.request(call, parameters)

            parameters = {'action': 'list', 'scan_ref': self.ref, 'show_status': 1}
            self.status = lxml.objectify.fromstring(conn.request(call, parameters)).RESPONSE.SCAN_LIST.SCAN.STATUS.STATE


class MapReportRecord(CacheableQualysObject):
    '''
    Wraps individual records in a MapReport.
    '''
    pass


class RequestEcho(CacheableQualysObject):
    '''
    A wrapper for embedded request echo in response request.
    ::
       <!ELEMENT REQUEST (DATETIME, USER_LOGIN, RESOURCE, PARAM_LIST?,
                          POST_DATA?)>
       <!ELEMENT DATETIME (#PCDATA)>
       <!ELEMENT USER_LOGIN (#PCDATA)>
       <!ELEMENT RESOURCE (#PCDATA)>
       <!ELEMENT PARAM_LIST (PARAM+)>
       <!ELEMENT PARAM (KEY, VALUE)>
       <!ELEMENT KEY (#PCDATA)>
       <!ELEMENT VALUE (#PCDATA)>
       <!-- If specified, POST_DATA will be urlencoded -->
       <!ELEMENT POST_DATA (#PCDATA)>
    '''
    datetime = None
    user_login = None
    resource = None

    def __init__(self, *args, **kwargs):
        # for now this is only stubbed and very basic...
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'DATETIME': ('datetime', unicode_str),
                'USER_LOGIN': ('user_login', unicode_str),
                'RESOURCE': ('resource', unicode_str),
            })
        else:
            self.datetime = kwargs.pop('DATETIME', None)
            self.user_login = kwargs.pop('USER_LOGIN', None)
            self.resource = kwargs.pop('RESOURCE', None)
        super(RequestEcho, self).__init__(*args, **kwargs)


class ResponseItem(CacheableQualysObject):
    key = None
    value = None

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'KEY': ('key', unicode_str),
                'value': ('value', unicode_str),
            })
        else:
            self.key = kwargs.pop('key', None)
            self.value = kwargs.pop('value', None)
        super(ResponseItem, self).__init__(*args, **kwargs)


class Response(CacheableQualysObject):
    reponse_time = None
    response_text = None
    response_code = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'DATETIME': ('reponse_time', unicode_str),
            'CODE': ('response_code', unicode_str),
            'TEXT': ('response_text', unicode_str)
        })
        super(Response, self).__init__(*args, **kwargs)


class ApplianceResponse(CacheableQualysObject):
    id = None
    friendly_name = None
    activation_code = None
    remaining_qvsa_licenses = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'ID': ('id', unicode_str),
            'FRIENDLY_NAME': ('friendly_name', unicode_str),
            'ACTIVATION_CODE': ('activation_code', unicode_str),
            'REMAINING_QVSA_LICENSES': ('remaining_qvsa_licenses', unicode_str),
        })
        super(ApplianceResponse, self).__init__(*args, **kwargs)


class SimpleReturn(CacheableQualysObject):
    '''A wrapper for qualys responses to api commands (as opposed to requests).

    Properties:
    :property response_time:
        Response header timestamp.
    :property response_text:
        Response header text.
    :property response_items:
        A list of key/value pairs returned with the header.  This
    isn't private, but it should be considered protected.  Mostly.

    DTD associated with this class:
    ::
       <!-- QUALYS SIMPLE_RETURN DTD -->
       <!ELEMENT SIMPLE_RETURN (REQUEST?, RESPONSE)>
       <!ELEMENT REQUEST (DATETIME, USER_LOGIN, RESOURCE, PARAM_LIST?,
                          POST_DATA?)>
       <!ELEMENT DATETIME (#PCDATA)>
       <!ELEMENT USER_LOGIN (#PCDATA)>
       <!ELEMENT RESOURCE (#PCDATA)>
       <!ELEMENT PARAM_LIST (PARAM+)>
       <!ELEMENT PARAM (KEY, VALUE)>
       <!ELEMENT KEY (#PCDATA)>
       <!ELEMENT VALUE (#PCDATA)>
       <!-- If specified, POST_DATA will be urlencoded -->
       <!ELEMENT POST_DATA (#PCDATA)>
       <!ELEMENT RESPONSE (DATETIME, CODE?, TEXT, ITEM_LIST?)>
       <!ELEMENT CODE (#PCDATA)>
       <!ELEMENT TEXT (#PCDATA)>
       <!ELEMENT ITEM_LIST (ITEM+)>
       <!ELEMENT ITEM (KEY, VALUE*)>
    '''
    response = None
    appliance = None
    items = None
    __is_error = False
    __err_msg = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'RESPONSE': ('response', Response),
            'APPLIANCE': ('appliance', ApplianceResponse),
            'ITEM_LIST': ('items', ObjTypeList(ResponseItem,
                                               xpath='ITEM'))
        })
        super(SimpleReturn, self).__init__(*args, **kwargs)

    def checkStatus(self, raiseApiException=False):
        '''A wrapper around the response status attribute that should handle
        all of the various api responses the same.'''
        if self.response.response_text and 'Missing required parameter' in \
                self.response.response_text:
            self.__is_error = True
            self.__err_msg = 'A required parameter was missing from the API  \
                    request'

    def hasItem(self, key):
        '''Check for a key/value pair'''
        return True if key in self.response_items else False

    def getItemValue(self, key, default=None):
        '''hook for dict.get to callers'''
        return self.response_items.get(key, default)

    def getItemKeys(self):
        '''hook for dict.keys to callers'''
        return self.response_items.keys()

    def wasSuccessful(self):
        '''A bit more complicated than a simple 200 response, this method
        attempts to unify multiple types of responses into a unified
        success/fail test.  Child classes can extend this for additional
        conditions that include response codes, different response texts and
        anything else useful for a unilateral true/false.
        '''
        return True if not self.__is_error else False

    def raiseAPIExceptions(self):
        ''' raise any Qualys API exceptions '''
        if self.__is_error:
            raise exceptions.QualysException(self.__err_msg)


class QualysUser(CacheableQualysObject):
    ''' Common shared wrapper class for a User representation of the User
    element.
    ::
        <!ELEMENT LOGIN     (# PCDATA)>
        <!ELEMENT FIRSTNAME (# PCDATA)>
        <!ELEMENT LASTNAME  (# PCDATA)>
    Params
    login     -- username
    :property firstname:
        frist... name
    lastname  -- last... name
    '''
    login = ''
    firstname = ''
    lastname = ''

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'LOGIN': ('login', unicode_str),
                'FIRSTNAME': ('firstname', unicode_str),
                'LASTNAME': ('lastname', unicode_str),
            })
        else:
            self.login = kwargs.pop('LOGIN', None)
            self.firstname = kwargs.pop('FIRSTNAME', None)
            self.lastname = kwargs.pop('LASTNAME', None)
        super(QualysUser, self).__init__(*args, **kwargs)


class ReportTemplate(CacheableQualysObject):
    ''' Wrapper class for a report template

    DTD:
    ::
        <!ELEMENT REPORT_TEMPLATE (ID,
                TYPE,
                TEMPLATE_TYPE,
                TITLE,
                USER,
                LAST_UPDATE,
                GLOBAL,
                DEFAULT?)>
            <!ELEMENT ID (#PCDATA)>
            <!ELEMENT TYPE (#PCDATA)>
            <!ELEMENT TEMPLATE_TYPE (#PCDATA)>
            <!ELEMENT TITLE (#PCDATA)>
            <!ELEMENT USER (LOGIN, FIRSTNAME, LASTNAME)>
            <!ELEMENT LAST_UPDATE (#PCDATA)>
            <!ELEMENT GLOBAL (#PCDATA)>
            <!ELEMENT DEFAULT (#PCDATA)>```
    '''
    template_id = None
    report_type = None
    template_type = None
    title = None
    user = None
    last_update = None
    is_global = False
    is_default = False

    def __init__(self, *args, **kwargs):
        if 'elem' in kwargs or 'xml' in kwargs:
            param_map = {}
            if 'param_map' in kwargs:
                param_map = kwargs.pop('param_map', {})
            kwargs['param_map'] = param_map
            kwargs['param_map'].update({
                'ID': ('template_id', unicode_str),
                'TYPE': ('report_type', unicode_str),
                'TEMPLATE_TYPE': ('template_type', unicode_str),
                'TITLE': ('title', unicode_str),
                'USER': ('user', QualysUser),
                'LAST_UPDATE': ('last_update', unicode_str),
                'GLOBAL': ('is_global', bool),
                'DEFAULT': ('is_default', bool),
            })
        else:
            self.template_id = kwargs.pop('ID', self.template_id)
            self.report_type = kwargs.pop('TYPE', self.report_type)
            self.template_type = kwargs.pop('TEMPLATE_TYPE', self.template_type)
            self.title = kwargs.pop('TITLE', self.title)
            self.user = \
                QualysUser(**(kwargs.pop('USER', {})))
            self.last_update = kwargs.pop('LAST_UPDATE', self.last_update)
            self.is_global = kwargs.pop('GLOBAL', self.is_global)
            self.is_default = kwargs.pop('DEFAULT', self.is_default)
        super(ReportTemplate, self).__init__(*args, **kwargs)


class IPRange(CacheableQualysObject):
    '''Defines and handles an IP range.
    Params:
    ::
        <!ELEMENT RANGE (START, END)>
            <!ATTLIST RANGE network_id  CDATA #IMPLIED>
            <!ELEMENT START (#PCDATA)>
            <!ELEMENT END (#PCDATA)>
    :property network_id: str from <!ATTLIST RANGE network_id  CDATA #IMPLIED>
    :property start: str from <!ELEMENT START (#PCDATA)>
    :property end: str from <!ELEMENT END (#PCDATA)>
    '''

    def __init__(self, *args, **kwargs):
        self.network_id = kwargs.pop('network_id', None)
        self.start = kwargs.pop('START', None)
        self.end = kwargs.pop('END', None)
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'network_id': ('network_id', unicode_str),
            'START': ('start', unicode_str),
            'END': ('end', unicode_str),
        })
        super(IPRange, self).__init__(*args, **kwargs)


# class AssetGroup(CacheableQualysObject):
#     '''A wrapper around an asset group that includes the name and a list of IP
#     ranges that define the assets.
#     :property title:
#         from ASSET_GROUP_TITLE tags
#     :property ranges:
#         a list of IP ranges
#     '''
#     def __init__(self, *args, **kwargs):
#         title = kwargs.pop('ASSET_GROUP_TITLE', None)
#         ranges = kwargs.pop('USER_IP_LIST', None)
#         param_map = {}
#         if 'param_map' in kwargs:
#             param_map = kwargs.pop('param_map', {})
#         kwargs['param_map'] = param_map
#         kwargs['param_map'].update({
#                 'ASSET_GROUP_TITLE' : ('title',  str),
#                 'RANGE'             : ('ranges', list),
#         })
#         super(AssetGroup, self).__init__(*args, **kwargs)
#
#
class ReportTarget(CacheableQualysObject):
    '''Handles REPORT_TARGET part of a ReportHeader
    ::
        <!ELEMENT TARGET (USER_ASSET_GROUPS?, USER_IP_LIST?, COMBINED_IP_LIST?, ASSET_TAG_LIST?)>
            <!ELEMENT USER_ASSET_GROUPS (ASSET_GROUP_TITLE+)>
            <!ELEMENT USER_IP_LIST (RANGE*)>
            <!ELEMENT COMBINED_IP_LIST (RANGE*)>

    :property user_asset_groups: list of str from <!ELEMENT USER_ASSET_GROUPS (ASSET_GROUP_TITLE+)>
    :property user_ip_list: List of IPRange objects from <!ELEMENT USER_IP_LIST (RANGE*)>
    :property combined_ip_list: list of IPRange objects from <!ELEMENT COMBINED_IP_LIST (RANGE*)>
    :property included_tags: list of str from <!ELEMENT ASSET_TAG_LIST (INCLUDED_TAGS, EXCLUDED_TAGS?)>
    :property excluded_tags: list of str from 
    '''
    asset_groups = None
    ranges = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'USER_ASSET_GROUPS': ('asset_groups', ObjTypeList(unicode_str,
                                                              xpath="/ASSET_GROUP_TITLE")),
            'USER_IP_LIST': ('ranges', ObjTypeList(IPRange)),
        })
        super(ReportTarget, self).__init__(*args, **kwargs)


class AssetTag(CacheableQualysObject):
    """AssetTag
    asset tag id/name pair object
    """
    tag = None
    tag_id = None
    name = None
    color = None
    background_color = None

    def __init__(self, *args, **kwargs):
        scope = kwargs.pop('scope', None)
        tags = kwargs.pop('ASSET_TAG', None)
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'TAG': ('tag', unicode_str),
            'TAG_ID': ('tag_id', unicode_str),
            'NAME': ('name', unicode_str),
            'COLOR': ('color', unicode_str),
            'BACKGROUND_COLOR': ('background_color', unicode_str),
        })
        super(AssetTag, self).__init__(*args, **kwargs)


class AssetTagSet(CacheableQualysObject):
    '''A list of asset tag strings, a scope attribute, and useful functions.
    :property scope: A string delimiter from the scope attribute of a list of asset tags from Qualys.
    :property tags: List of str
    '''

    def __init__(self, *args, **kwargs):
        scope = kwargs.pop('scope', None)
        tags = kwargs.pop('ASSET_TAG', None)
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'scope': ('scope', unicode_str),
            'ASSET_TAG': ('tags', list),
        })
        super(AssetTagSet, self).__init__(*args, **kwargs)


class ReportHeader(CacheableQualysObject):
    '''Handles Report Headers
    ::
        <!ELEMENT HEADER (COMPANY, USERNAME, GENERATION_DATETIME, TEMPLATE, TARGET, RISK_SCORE_SUMMARY?)>

            <!ELEMENT COMPANY (#PCDATA)>
            <!ELEMENT USERNAME (#PCDATA)>
            <!ELEMENT GENERATION_DATETIME (#PCDATA)>
            <!ELEMENT TEMPLATE (#PCDATA)>
    '''
    company = None
    username = None
    generation_datetime = None
    template = None
    target = None
    included_tags = None
    excluded_tags = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'COMPANY': ('company', unicode_str),
            'USERNAME': ('username', unicode_str),
            'GENERATION_DATETIME': ('generation_datetime', unicode_str),
            'TEMPLATE': ('template', unicode_str),
            'TARGET': ('target', ReportTarget),
            'ASSET_TAG_LIST': ({
                                   ('included_tags', ObjTypeList(AssetTagSet,
                                                                 xpath='INCLUDED_TAGS')),
                                   ('excluded_tags', ObjTypeList(AssetTagSet,
                                                                 xpath='EXCLUDED_TAGS')), }, dict),
        })
        super(ReportHeader, self).__init__(*args, **kwargs)


class AssetDataReport(Report):
    '''A wrapper around a qualys report.
    .. seealso::
        Header
        Host
    '''
    header = None  #: header object from HEADER element
    hosts = None  #: list of Host objects

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'HEADER': ('header', ReportHeader),
            'HOST_LIST': ('hosts', ObjTypeList(Host, xpath='HOST')),
        })
        super(AssetDataReport, self).__init__(*args, **kwargs)
        # if no hosts were assigned, go ahead and init an empty list
        if self.hosts is None:
            self.hosts = []

    def addHosts(self, hosts):
        """addHosts

        :param hosts:
            one or more hosts to append to this report.  hosts can be a list of
            Host objects or simple a single Host object.
        """
        self.hosts.append(hosts)


class AssetWarning(CacheableQualysObject):
    '''Qualys WARNING element handler for most Asset API calls.
    ::
        <!ELEMENT WARNING (CODE?, TEXT, URL?)>
        <!ELEMENT CODE (#PCDATA)>
        <!ELEMENT TEXT (#PCDATA)>
        <!ELEMENT URL (#PCDATA)>
    '''
    code = None
    text = None
    url = None

    def __init__(self, *args, **kwargs):
        param_map = {}
        if 'param_map' in kwargs:
            param_map = kwargs.pop('param_map', {})
        kwargs['param_map'] = param_map
        kwargs['param_map'].update({
            'CODE': ('code', unicode_str),
            'TEXT': ('text', unicode_str),
            'URL': ('url', unicode_str),
        })
        super(AssetWarning, self).__init__(*args, **kwargs)

    def getQueryDict(self):
        return dict(urlparse.parse_qsl(urlparse.urlsplit(self.url).query))


class ImportBuffer(object):
    """ImportBuffer
    Base class for import buffers (extended result handling methods)

    This class is iterable.  SMP/MT classes should implement alternate
    iterators for queues if their queues are iterable.
    """
    results_list = None

    def __init__(self, *args, **kwargs):
        self.results_list = []
        super(ImportBuffer, self).__init__()

    def add(self, item):
        '''Place a new object into the buffer'''
        # TODO: only put an item in the queue if it is process deferred,
        # otherwise put it into a simple list to return immediately.
        # logger.debug('Adding item to results_list of type \'%s\'' % type(item))
        self.results_list.append(item)

    def __iter__(self):
        return iter(self.results_list)

    def __str__(self):
        return pprint.pformat(self.results_list)

    def finish(self, block=True, **kwargs):
        """finish

        This method is a stub for MP/MT child classes.  It allows a standard
        way to ensure processing is finished before cleanup.

        :param block:
        for asynchronous processing children, this induces blocking to return
        results.  Otherwise it just returns non-async results for efficiency.

        This allows selective defer for MT/MP processes.
        """

        return self.results_list


# element to api_object mapping
# this is temporary in lieu of an object which allows for user-override of
# parse object (subclass parse consumers)
# TODO: tweak this for queue handling of specific types
obj_elem_map = {
    'MAP_REPORT': Map,
    'MAP_RESULT': MapResult,
    'VULN': QKBVuln,
    'REPORT': Report,
    'REPORT_TEMPLATE': ReportTemplate,
    'SIMPLE_RETURN': SimpleReturn,
    'ASSET_DATA_REPORT': AssetDataReport,
    'ASSET_GROUP_LIST': AssetGroupList,
    # this is disabled (for now)
    'HOST': Host,
    'WARNING': AssetWarning,
}

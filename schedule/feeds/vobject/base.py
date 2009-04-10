"""vobject module for reading vCard and vCalendar files."""

import copy
import re
import sys
import logging
import StringIO, cStringIO
import string
import exceptions
import codecs

#------------------------------------ Logging ----------------------------------
logger = logging.getLogger(__name__)
if not logging.getLogger().handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(name)s %(levelname)s %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.ERROR) # Log errors
DEBUG = False # Don't waste time on debug calls
#----------------------------------- Constants ---------------------------------
CR     = '\r'
LF     = '\n'
CRLF   = CR + LF
SPACE  = ' '
TAB    = '\t'
SPACEORTAB = SPACE + TAB
#-------------------------------- Useful modules -------------------------------
#   use doctest, it kills two birds with one stone and docstrings often become
#                more readable to boot (see parseLine's docstring).
#   use logging, then when debugging we can just set our verbosity.
#   use epydoc syntax for documenting code, please document every class and non-
#                trivial method (see http://epydoc.sourceforge.net/epytext.html
#                and http://epydoc.sourceforge.net/fields.html).  Also, please
#                follow http://www.python.org/peps/pep-0257.html for docstrings.
#-------------------------------------------------------------------------------

#--------------------------------- Main classes --------------------------------
class VBase(object):
    """Base class for ContentLine and Component.
    
    @ivar behavior:
        The Behavior class associated with this object, which controls
        validation, transformations, and encoding.
    @ivar parentBehavior:
        The object's parent's behavior, or None if no behaviored parent exists.
    @ivar isNative:
        Boolean describing whether this component is a Native instance.
    @ivar group:
        An optional group prefix, should be used only to indicate sort order in 
        vCards, according to RFC2426
    """
    def __init__(self, group=None, *args, **kwds):
        super(VBase, self).__init__(*args, **kwds)
        self.group      = group
        self.behavior   = None
        self.parentBehavior = None
        self.isNative = False
    
    def copy(self, copyit):
        self.group = copyit.group
        self.behavior = copyit.behavior
        self.parentBehavior = copyit.parentBehavior
        self.isNative = copyit.isNative
        
    def validate(self, *args, **kwds):
        """Call the behavior's validate method, or return True."""
        if self.behavior:
            return self.behavior.validate(self, *args, **kwds)
        else: return True

    def getChildren(self):
        """Return an iterable containing the contents of the object."""
        return []

    def clearBehavior(self, cascade=True):
        """Set behavior to None. Do for all descendants if cascading."""
        self.behavior=None
        if cascade: self.transformChildrenFromNative()

    def autoBehavior(self, cascade=False):
        """Set behavior if name is in self.parentBehavior.knownChildren.
        
        If cascade is True, unset behavior and parentBehavior for all
        descendants, then recalculate behavior and parentBehavior.
        
        """
        parentBehavior = self.parentBehavior
        if parentBehavior is not None:
            knownChildTup = parentBehavior.knownChildren.get(self.name, None)
            if knownChildTup is not None:
                behavior = getBehavior(self.name, knownChildTup[2])
                if behavior is not None:
                    self.setBehavior(behavior, cascade)
                    if isinstance(self, ContentLine) and self.encoded:
                        self.behavior.decode(self)
            elif isinstance(self, ContentLine):
                self.behavior = parentBehavior.defaultBehavior   
                if self.encoded and self.behavior:
                    self.behavior.decode(self)

    def setBehavior(self, behavior, cascade=True):
        """Set behavior. If cascade is True, autoBehavior all descendants."""
        self.behavior=behavior
        if cascade:
            for obj in self.getChildren():
                obj.parentBehavior=behavior
                obj.autoBehavior(True)

    def transformToNative(self):
        """Transform this object into a custom VBase subclass.
        
        transformToNative should always return a representation of this object.
        It may do so by modifying self in place then returning self, or by
        creating a new object.
        
        """
        if self.isNative or not self.behavior or not self.behavior.hasNative:
            return self
        else:
            try:
                return self.behavior.transformToNative(self)
            except Exception, e:      
                # wrap errors in transformation in a ParseError
                lineNumber = getattr(self, 'lineNumber', None)
                if isinstance(e, ParseError):
                    if lineNumber is not None:
                        e.lineNumber = lineNumber
                    raise
                else:
                    msg = "In transformToNative, unhandled exception: %s: %s"
                    msg = msg % (sys.exc_info()[0], sys.exc_info()[1])
                    new_error = ParseError(msg, lineNumber)
                    raise ParseError, new_error, sys.exc_info()[2]
                

    def transformFromNative(self):
        """Return self transformed into a ContentLine or Component if needed.
        
        May have side effects.  If it does, transformFromNative and
        transformToNative MUST have perfectly inverse side effects. Allowing
        such side effects is convenient for objects whose transformations only
        change a few attributes.
        
        Note that it isn't always possible for transformFromNative to be a
        perfect inverse of transformToNative, in such cases transformFromNative
        should return a new object, not self after modifications.
        
        """
        if self.isNative and self.behavior and self.behavior.hasNative:
            try:
                return self.behavior.transformFromNative(self)
            except Exception, e:
                # wrap errors in transformation in a NativeError
                lineNumber = getattr(self, 'lineNumber', None)
                if isinstance(e, NativeError):
                    if lineNumber is not None:
                        e.lineNumber = lineNumber
                    raise
                else:
                    msg = "In transformFromNative, unhandled exception: %s: %s"
                    msg = msg % (sys.exc_info()[0], sys.exc_info()[1])
                    new_error = NativeError(msg, lineNumber)
                    raise NativeError, new_error, sys.exc_info()[2]
        else: return self

    def transformChildrenToNative(self):
        """Recursively replace children with their native representation."""
        pass

    def transformChildrenFromNative(self, clearBehavior=True):
        """Recursively transform native children to vanilla representations."""
        pass

    def serialize(self, buf=None, lineLength=75, validate=True, behavior=None):
        """Serialize to buf if it exists, otherwise return a string.
        
        Use self.behavior.serialize if behavior exists.
        
        """
        if not behavior:
            behavior = self.behavior
        
        if behavior:
            if DEBUG: logger.debug("serializing %s with behavior" % self.name)
            return behavior.serialize(self, buf, lineLength, validate)
        else:
            if DEBUG: logger.debug("serializing %s without behavior" % self.name)
            return defaultSerialize(self, buf, lineLength)

def ascii(s):
    """Turn s into a printable string.  Won't work for 8-bit ASCII."""
    return unicode(s).encode('ascii', 'replace')

def toVName(name, stripNum = 0, upper = False):
    """
    Turn a Python name into an iCalendar style name, optionally uppercase and 
    with characters stripped off.
    """
    if upper:
        name = name.upper()
    if stripNum != 0:
        name = name[:-stripNum]
    return name.replace('_', '-')

class ContentLine(VBase):
    """Holds one content line for formats like vCard and vCalendar.

    For example::
      <SUMMARY{u'param1' : [u'val1'], u'param2' : [u'val2']}Bastille Day Party>

    @ivar name:
        The uppercased name of the contentline.
    @ivar params:
        A dictionary of parameters and associated lists of values (the list may
        be empty for empty parameters).
    @ivar value:
        The value of the contentline.
    @ivar singletonparams:
        A list of parameters for which it's unclear if the string represents the
        parameter name or the parameter value. In vCard 2.1, "The value string
        can be specified alone in those cases where the value is unambiguous".
        This is crazy, but we have to deal with it.
    @ivar encoded:
        A boolean describing whether the data in the content line is encoded.
        Generally, text read from a serialized vCard or vCalendar should be
        considered encoded.  Data added programmatically should not be encoded.
    @ivar lineNumber:
        An optional line number associated with the contentline.
    """
    def __init__(self, name, params, value, group=None, 
                 encoded=False, isNative=False,
                 lineNumber = None, *args, **kwds):
        """Take output from parseLine, convert params list to dictionary."""
        # group is used as a positional argument to match parseLine's return
        super(ContentLine, self).__init__(group, *args, **kwds)
        self.name        = name.upper()
        self.value       = value
        self.encoded     = encoded
        self.params      = {}
        self.singletonparams = []
        self.isNative = isNative
        self.lineNumber = lineNumber
        def updateTable(x):
            if len(x) == 1:
                self.singletonparams += x
            else:
                paramlist = self.params.setdefault(x[0].upper(), [])
                paramlist.extend(x[1:])
        map(updateTable, params)
        qp = False
        if 'ENCODING' in self.params:
            if 'QUOTED-PRINTABLE' in self.params['ENCODING']:
                qp = True
                self.params['ENCODING'].remove('QUOTED-PRINTABLE')
                if 0==len(self.params['ENCODING']):
                    del self.params['ENCODING']
        if 'QUOTED-PRINTABLE' in self.singletonparams:
            qp = True
            self.singletonparams.remove('QUOTED-PRINTABLE')
        if qp:
            self.value = str(self.value).decode('quoted-printable')

        # self.value should be unicode for iCalendar, but if quoted-printable
        # is used, or if the quoted-printable state machine is used, text may be
        # encoded
        if type(self.value) is str:
            charset = 'iso-8859-1'
            if 'CHARSET' in self.params:
                charsets = self.params.pop('CHARSET')
                if charsets:
                    charset = charsets[0]
            self.value = unicode(self.value, charset)

    @classmethod
    def duplicate(clz, copyit):
        newcopy = clz('', {}, '')
        newcopy.copy(copyit)
        return newcopy

    def copy(self, copyit):
        super(ContentLine, self).copy(copyit)
        self.name = copyit.name
        self.value = copy.copy(copyit.value)
        self.encoded = self.encoded
        self.params = copy.copy(copyit.params)
        self.singletonparams = copy.copy(copyit.singletonparams)
        self.lineNumber = copyit.lineNumber
        
    def __eq__(self, other):
        try:
            return (self.name == other.name) and (self.params == other.params) and (self.value == other.value)
        except:
            return False

    def _getAttributeNames(self):
        """Return a list of attributes of the object.

           Python 2.6 will add __dir__ to customize what attributes are returned
           by dir, for now copy PyCrust so that IPython can accurately do
           completion.

        """
        keys = self.params.keys()
        params = [param + '_param' for param in keys]
        params.extend(param + '_paramlist' for param in keys)
        return params

    def __getattr__(self, name):
        """Make params accessible via self.foo_param or self.foo_paramlist.

        Underscores, legal in python variable names, are converted to dashes,
        which are legal in IANA tokens.

        """
        try:
            if name.endswith('_param'):
                return self.params[toVName(name, 6, True)][0]
            elif name.endswith('_paramlist'):
                return self.params[toVName(name, 10, True)]
            else:
                raise exceptions.AttributeError, name
        except KeyError:
            raise exceptions.AttributeError, name

    def __setattr__(self, name, value):
        """Make params accessible via self.foo_param or self.foo_paramlist.

        Underscores, legal in python variable names, are converted to dashes,
        which are legal in IANA tokens.
        
        """
        if name.endswith('_param'):
            if type(value) == list:
                self.params[toVName(name, 6, True)] = value
            else:
                self.params[toVName(name, 6, True)] = [value]
        elif name.endswith('_paramlist'):
            if type(value) == list:
                self.params[toVName(name, 10, True)] = value
            else:
                raise VObjectError("Parameter list set to a non-list")
        else:
            prop = getattr(self.__class__, name, None)
            if isinstance(prop, property):
                prop.fset(self, value)
            else:
                object.__setattr__(self, name, value)

    def __delattr__(self, name):
        try:
            if name.endswith('_param'):
                del self.params[toVName(name, 6, True)]
            elif name.endswith('_paramlist'):
                del self.params[toVName(name, 10, True)]
            else:
                object.__delattr__(self, name)
        except KeyError:
            raise exceptions.AttributeError, name

    def valueRepr( self ):
        """transform the representation of the value according to the behavior,
        if any"""
        v = self.value
        if self.behavior:
            v = self.behavior.valueRepr( self )
        return ascii( v )
        
    def __str__(self):
        return "<"+ascii(self.name)+ascii(self.params)+self.valueRepr()+">"

    def __repr__(self):
        return self.__str__().replace('\n', '\\n')

    def prettyPrint(self, level = 0, tabwidth=3):
        pre = ' ' * level * tabwidth
        print pre, self.name + ":", self.valueRepr()
        if self.params:
            lineKeys= self.params.keys()
            print pre, "params for ", self.name +':'
            for aKey in lineKeys:
                print pre + ' ' * tabwidth, aKey, ascii(self.params[aKey])

class Component(VBase):
    """A complex property that can contain multiple ContentLines.
    
    For our purposes, a component must start with a BEGIN:xxxx line and end with
    END:xxxx, or have a PROFILE:xxx line if a top-level component.

    @ivar contents:
        A dictionary of lists of Component or ContentLine instances. The keys
        are the lowercased names of child ContentLines or Components.
        Note that BEGIN and END ContentLines are not included in contents.
    @ivar name:
        Uppercase string used to represent this Component, i.e VCARD if the
        serialized object starts with BEGIN:VCARD.
    @ivar useBegin:
        A boolean flag determining whether BEGIN: and END: lines should
        be serialized.

    """
    def __init__(self, name=None, *args, **kwds):
        super(Component, self).__init__(*args, **kwds)
        self.contents  = {}
        if name:
            self.name=name.upper()
            self.useBegin = True
        else:
            self.name = ''
            self.useBegin = False
        
        self.autoBehavior()

    @classmethod
    def duplicate(clz, copyit):
        newcopy = clz()
        newcopy.copy(copyit)
        return newcopy

    def copy(self, copyit):
        super(Component, self).copy(copyit)
        
        # deep copy of contents
        self.contents = {}
        for key, lvalue in copyit.contents.items():
            newvalue = []
            for value in lvalue:
                newitem = value.duplicate(value)
                newvalue.append(newitem)
            self.contents[key] = newvalue

        self.name = copyit.name
        self.useBegin = copyit.useBegin
         
    def setProfile(self, name):
        """Assign a PROFILE to this unnamed component.
        
        Used by vCard, not by vCalendar.
        
        """
        if self.name or self.useBegin:
            if self.name == name: return
            raise VObjectError("This component already has a PROFILE or uses BEGIN.")
        self.name = name.upper()

    def _getAttributeNames(self):
        """Return a list of attributes of the object.

           Python 2.6 will add __dir__ to customize what attributes are returned
           by dir, for now copy PyCrust so that IPython can accurately do
           completion.

        """
        names = self.contents.keys()
        names.extend(name + '_list' for name in self.contents.keys())
        return names

    def __getattr__(self, name):
        """For convenience, make self.contents directly accessible.
        
        Underscores, legal in python variable names, are converted to dashes,
        which are legal in IANA tokens.
        
        """
        # if the object is being re-created by pickle, self.contents may not
        # be set, don't get into an infinite loop over the issue
        if name == 'contents':
            return object.__getattribute__(self, name) 
        try:
            if name.endswith('_list'):
                return self.contents[toVName(name, 5)]
            else:
                return self.contents[toVName(name)][0]
        except KeyError:
            raise exceptions.AttributeError, name

    normal_attributes = ['contents','name','behavior','parentBehavior','group']
    def __setattr__(self, name, value):
        """For convenience, make self.contents directly accessible.

        Underscores, legal in python variable names, are converted to dashes,
        which are legal in IANA tokens.
        
        """
        if name not in self.normal_attributes and name.lower()==name:
            if type(value) == list:
                if name.endswith('_list'):
                    name = name[:-5]
                self.contents[toVName(name)] = value
            elif name.endswith('_list'):
                raise VObjectError("Component list set to a non-list")
            else:
                self.contents[toVName(name)] = [value]
        else:
            prop = getattr(self.__class__, name, None)
            if isinstance(prop, property):
                prop.fset(self, value)
            else:
                object.__setattr__(self, name, value)

    def __delattr__(self, name):
        try:
            if name not in self.normal_attributes and name.lower()==name:
                if name.endswith('_list'):
                    del self.contents[toVName(name, 5)]
                else:
                    del self.contents[toVName(name)]
            else:
                object.__delattr__(self, name)
        except KeyError:
            raise exceptions.AttributeError, name

    def getChildValue(self, childName, default = None, childNumber = 0):
        """Return a child's value (the first, by default), or None."""
        child = self.contents.get(toVName(childName))
        if child is None:
            return default
        else:
            return child[childNumber].value

    def add(self, objOrName, group = None):
        """Add objOrName to contents, set behavior if it can be inferred.
        
        If objOrName is a string, create an empty component or line based on
        behavior. If no behavior is found for the object, add a ContentLine.

        group is an optional prefix to the name of the object (see
        RFC 2425).
        """
        if isinstance(objOrName, VBase):
            obj = objOrName
            if self.behavior:
                obj.parentBehavior = self.behavior
                obj.autoBehavior(True)
        else:
            name = objOrName.upper()
            try:
                id=self.behavior.knownChildren[name][2]
                behavior = getBehavior(name, id)
                if behavior.isComponent:
                    obj = Component(name)
                else:
                    obj = ContentLine(name, [], '', group)
                obj.parentBehavior = self.behavior
                obj.behavior = behavior
                obj = obj.transformToNative()     
            except (KeyError, AttributeError):
                obj = ContentLine(objOrName, [], '', group)
            if obj.behavior is None and self.behavior is not None:
                if isinstance(obj, ContentLine):
                    obj.behavior = self.behavior.defaultBehavior
        self.contents.setdefault(obj.name.lower(), []).append(obj)
        return obj

    def remove(self, obj):
        """Remove obj from contents."""
        named = self.contents.get(obj.name.lower())
        if named:
            try:
                named.remove(obj)
                if len(named) == 0:
                    del self.contents[obj.name.lower()]
            except ValueError:
                pass;

    def getChildren(self):
        """Return an iterable of all children."""
        for objList in self.contents.values():
            for obj in objList: yield obj

    def components(self):
        """Return an iterable of all Component children."""
        return (i for i in self.getChildren() if isinstance(i, Component))

    def lines(self):
        """Return an iterable of all ContentLine children."""
        return (i for i in self.getChildren() if isinstance(i, ContentLine))

    def sortChildKeys(self):
        try:
            first = [s for s in self.behavior.sortFirst if s in self.contents]
        except:
            first = []
        return first + sorted(k for k in self.contents.keys() if k not in first)

    def getSortedChildren(self):
        return [obj for k in self.sortChildKeys() for obj in self.contents[k]]

    def setBehaviorFromVersionLine(self, versionLine):
        """Set behavior if one matches name, versionLine.value."""
        v=getBehavior(self.name, versionLine.value)
        if v: self.setBehavior(v)

    def transformChildrenToNative(self):
        """Recursively replace children with their native representation."""
        #sort to get dependency order right, like vtimezone before vevent
        for childArray in (self.contents[k] for k in self.sortChildKeys()):
            for i in xrange(len(childArray)):
                childArray[i]=childArray[i].transformToNative()
                childArray[i].transformChildrenToNative()

    def transformChildrenFromNative(self, clearBehavior=True):
        """Recursively transform native children to vanilla representations."""
        for childArray in self.contents.values():
            for i in xrange(len(childArray)):
                childArray[i]=childArray[i].transformFromNative()
                childArray[i].transformChildrenFromNative(clearBehavior)
                if clearBehavior:
                    childArray[i].behavior = None
                    childArray[i].parentBehavior = None
    
    def __str__(self):
        if self.name:
            return "<" + self.name + "| " + str(self.getSortedChildren()) + ">"
        else:
            return '<' + '*unnamed*' + '| ' + str(self.getSortedChildren()) + '>'

    def __repr__(self):
        return self.__str__()

    def prettyPrint(self, level = 0, tabwidth=3):
        pre = ' ' * level * tabwidth
        print pre, self.name
        if isinstance(self, Component):
            for line in self.getChildren():
                line.prettyPrint(level + 1, tabwidth)
        print

class VObjectError(Exception):
    def __init__(self, message, lineNumber=None):
        self.message = message
        if lineNumber is not None:
            self.lineNumber = lineNumber
    def __str__(self):
        if hasattr(self, 'lineNumber'):
            return "At line %s: %s" % \
                   (self.lineNumber, self.message)
        else:
            return repr(self.message)

class ParseError(VObjectError):
    pass

class ValidateError(VObjectError):
    pass

class NativeError(VObjectError):
    pass

#-------------------------- Parsing functions ----------------------------------

# parseLine regular expressions

patterns = {}

# Note that underscore is not legal for names, it's included because
# Lotus Notes uses it
patterns['name'] = '[a-zA-Z0-9\-_]+'                                  
patterns['safe_char'] = '[^";:,]'
patterns['qsafe_char'] = '[^"]'

# the combined Python string replacement and regex syntax is a little confusing;
# remember that %(foobar)s is replaced with patterns['foobar'], so for instance
# param_value is any number of safe_chars or any number of qsaf_chars surrounded
# by double quotes.

patterns['param_value'] = ' "%(qsafe_char)s * " | %(safe_char)s * ' % patterns


# get a tuple of two elements, one will be empty, the other will have the value
patterns['param_value_grouped'] = """
" ( %(qsafe_char)s * )" | ( %(safe_char)s + )
""" % patterns

# get a parameter and its values, without any saved groups
patterns['param'] = r"""
; (?: %(name)s )                     # parameter name
(?:
    (?: = (?: %(param_value)s ) )?   # 0 or more parameter values, multiple 
    (?: , (?: %(param_value)s ) )*   # parameters are comma separated
)*                         
""" % patterns

# get a parameter, saving groups for name and value (value still needs parsing)
patterns['params_grouped'] = r"""
; ( %(name)s )

(?: =
    (
        (?:   (?: %(param_value)s ) )?   # 0 or more parameter values, multiple 
        (?: , (?: %(param_value)s ) )*   # parameters are comma separated
    )
)?
""" % patterns

# get a full content line, break it up into group, name, parameters, and value
patterns['line'] = r"""
^ ((?P<group> %(name)s)\.)?(?P<name> %(name)s) # name group
  (?P<params> (?: %(param)s )* )               # params group (may be empty)
: (?P<value> .* )$                             # value group
""" % patterns

' "%(qsafe_char)s*" | %(safe_char)s* '

param_values_re = re.compile(patterns['param_value_grouped'], re.VERBOSE)
params_re       = re.compile(patterns['params_grouped'],      re.VERBOSE)
line_re         = re.compile(patterns['line'],    re.DOTALL | re.VERBOSE)
begin_re        = re.compile('BEGIN', re.IGNORECASE)


def parseParams(string):
    """
    >>> parseParams(';ALTREP="http://www.wiz.org"')
    [['ALTREP', 'http://www.wiz.org']]
    >>> parseParams('')
    []
    >>> parseParams(';ALTREP="http://www.wiz.org;;",Blah,Foo;NEXT=Nope;BAR')
    [['ALTREP', 'http://www.wiz.org;;', 'Blah', 'Foo'], ['NEXT', 'Nope'], ['BAR']]
    """
    all = params_re.findall(string)
    allParameters = []
    for tup in all:
        paramList = [tup[0]] # tup looks like (name, valuesString)
        for pair in param_values_re.findall(tup[1]):
            # pair looks like ('', value) or (value, '')
            if pair[0] != '':
                paramList.append(pair[0])
            else:
                paramList.append(pair[1])
        allParameters.append(paramList)
    return allParameters


def parseLine(line, lineNumber = None):
    """
    >>> parseLine("BLAH:")
    ('BLAH', [], '', None)
    >>> parseLine("RDATE:VALUE=DATE:19970304,19970504,19970704,19970904")
    ('RDATE', [], 'VALUE=DATE:19970304,19970504,19970704,19970904', None)
    >>> parseLine('DESCRIPTION;ALTREP="http://www.wiz.org":The Fall 98 Wild Wizards Conference - - Las Vegas, NV, USA')
    ('DESCRIPTION', [['ALTREP', 'http://www.wiz.org']], 'The Fall 98 Wild Wizards Conference - - Las Vegas, NV, USA', None)
    >>> parseLine("EMAIL;PREF;INTERNET:john@nowhere.com")
    ('EMAIL', [['PREF'], ['INTERNET']], 'john@nowhere.com', None)
    >>> parseLine('EMAIL;TYPE="blah",hah;INTERNET="DIGI",DERIDOO:john@nowhere.com')
    ('EMAIL', [['TYPE', 'blah', 'hah'], ['INTERNET', 'DIGI', 'DERIDOO']], 'john@nowhere.com', None)
    >>> parseLine('item1.ADR;type=HOME;type=pref:;;Reeperbahn 116;Hamburg;;20359;')
    ('ADR', [['type', 'HOME'], ['type', 'pref']], ';;Reeperbahn 116;Hamburg;;20359;', 'item1')
    >>> parseLine(":")
    Traceback (most recent call last):
    ...
    ParseError: 'Failed to parse line: :'
    """
    
    match = line_re.match(line)
    if match is None:
        raise ParseError("Failed to parse line: %s" % line, lineNumber)
    # Underscores are replaced with dash to work around Lotus Notes
    return (match.group('name').replace('_','-'),                                 
            parseParams(match.group('params')),
            match.group('value'), match.group('group'))

# logical line regular expressions

patterns['lineend'] = r'(?:\r\n|\r|\n|$)'
patterns['wrap'] = r'%(lineend)s [\t ]' % patterns
patterns['logicallines'] = r"""
(
   (?: [^\r\n] | %(wrap)s )*
   %(lineend)s
)
""" % patterns

patterns['wraporend'] = r'(%(wrap)s | %(lineend)s )' % patterns

wrap_re          = re.compile(patterns['wraporend'],    re.VERBOSE)
logical_lines_re = re.compile(patterns['logicallines'], re.VERBOSE)

testLines="""
Line 0 text
 , Line 0 continued.
Line 1;encoding=quoted-printable:this is an evil=
 evil=
 format.
Line 2 is a new line, it does not start with whitespace.
"""

def getLogicalLines(fp, allowQP=True, findBegin=False):
    """Iterate through a stream, yielding one logical line at a time.

    Because many applications still use vCard 2.1, we have to deal with the
    quoted-printable encoding for long lines, as well as the vCard 3.0 and
    vCalendar line folding technique, a whitespace character at the start
    of the line.
       
    Quoted-printable data will be decoded in the Behavior decoding phase.
       
    >>> import StringIO
    >>> f=StringIO.StringIO(testLines)
    >>> for n, l in enumerate(getLogicalLines(f)):
    ...     print "Line %s: %s" % (n, l[0])
    ...
    Line 0: Line 0 text, Line 0 continued.
    Line 1: Line 1;encoding=quoted-printable:this is an evil=
     evil=
     format.
    Line 2: Line 2 is a new line, it does not start with whitespace.

    """
    if not allowQP:
        bytes = fp.read(-1)
        if len(bytes) > 0:
            if type(bytes[0]) == unicode:
                val = bytes
            elif not findBegin:
                val = bytes.decode('utf-8')
            else:
                for encoding in 'utf-8', 'utf-16-LE', 'utf-16-BE', 'iso-8859-1':
                    try:
                        val = bytes.decode(encoding)
                        if begin_re.search(val) is not None:
                            break
                    except UnicodeDecodeError:
                        pass
                else:
                    raise ParseError, 'Could not find BEGIN when trying to determine encoding'
        else:
            val = bytes
        
        # strip off any UTF8 BOMs which Python's UTF8 decoder leaves

        val = val.lstrip( unicode( codecs.BOM_UTF8, "utf8" ) )

        lineNumber = 1
        for match in logical_lines_re.finditer(val):
            line, n = wrap_re.subn('', match.group())
            if line != '':
                yield line, lineNumber
            lineNumber += n
        
    else:
        quotedPrintable=False
        newbuffer = StringIO.StringIO
        logicalLine = newbuffer()
        lineNumber = 0
        lineStartNumber = 0
        while True:
            line = fp.readline()
            if line == '':
                break
            else:
                line = line.rstrip(CRLF)
                lineNumber += 1
            if line.rstrip() == '':
                if logicalLine.pos > 0:
                    yield logicalLine.getvalue(), lineStartNumber
                lineStartNumber = lineNumber
                logicalLine = newbuffer()
                quotedPrintable=False
                continue
    
            if quotedPrintable and allowQP:
                logicalLine.write('\n')
                logicalLine.write(line)
                quotedPrintable=False
            elif line[0] in SPACEORTAB:
                logicalLine.write(line[1:])
            elif logicalLine.pos > 0:
                yield logicalLine.getvalue(), lineStartNumber
                lineStartNumber = lineNumber
                logicalLine = newbuffer()
                logicalLine.write(line)
            else:
                logicalLine = newbuffer()
                logicalLine.write(line)
            
            # hack to deal with the fact that vCard 2.1 allows parameters to be
            # encoded without a parameter name.  False positives are unlikely, but
            # possible.
            val = logicalLine.getvalue()
            if val[-1]=='=' and val.lower().find('quoted-printable') >= 0:
                quotedPrintable=True
    
        if logicalLine.pos > 0:
            yield logicalLine.getvalue(), lineStartNumber


def textLineToContentLine(text, n=None):
    return ContentLine(*parseLine(text, n), **{'encoded':True, 'lineNumber' : n})
            

def dquoteEscape(param):
    """Return param, or "param" if ',' or ';' or ':' is in param."""
    if param.find('"') >= 0:
        raise VObjectError("Double quotes aren't allowed in parameter values.")
    for char in ',;:':
        if param.find(char) >= 0:
            return '"'+ param + '"'
    return param

def foldOneLine(outbuf, input, lineLength = 75):
    # Folding line procedure that ensures multi-byte utf-8 sequences are not broken
    # across lines

    if len(input) < lineLength:
        # Optimize for unfolded line case
        outbuf.write(input)
    else:
        # Look for valid utf8 range and write that out
        start = 0
        written = 0
        while written < len(input):
            # Start max length -1 chars on from where we are
            offset = start + lineLength - 1
            if offset >= len(input):
                line = input[start:]
                outbuf.write(line)
                written = len(input)
            else:
                # Check whether next char is valid utf8 lead byte
                while (input[offset] > 0x7F) and ((ord(input[offset]) & 0xC0) == 0x80):
                    # Step back until we have a valid char
                    offset -= 1
                
                line = input[start:offset]
                outbuf.write(line)
                outbuf.write("\r\n ")
                written += offset - start
                start = offset
    outbuf.write("\r\n")

def defaultSerialize(obj, buf, lineLength):
    """Encode and fold obj and its children, write to buf or return a string."""

    outbuf = buf or cStringIO.StringIO()

    if isinstance(obj, Component):
        if obj.group is None:
            groupString = ''
        else:
            groupString = obj.group + '.'
        if obj.useBegin:
            foldOneLine(outbuf, str(groupString + u"BEGIN:" + obj.name), lineLength)
        for child in obj.getSortedChildren():
            #validate is recursive, we only need to validate once
            child.serialize(outbuf, lineLength, validate=False)
        if obj.useBegin:
            foldOneLine(outbuf, str(groupString + u"END:" + obj.name), lineLength)
        
    elif isinstance(obj, ContentLine):
        startedEncoded = obj.encoded
        if obj.behavior and not startedEncoded: obj.behavior.encode(obj)
        s=codecs.getwriter('utf-8')(cStringIO.StringIO()) #unfolded buffer
        if obj.group is not None:
            s.write(obj.group + '.')
        s.write(obj.name.upper())
        for key, paramvals in obj.params.iteritems():
            s.write(';' + key + '=' + ','.join(dquoteEscape(p) for p in paramvals))
        s.write(':' + obj.value)
        if obj.behavior and not startedEncoded: obj.behavior.decode(obj)
        foldOneLine(outbuf, s.getvalue(), lineLength)
    
    return buf or outbuf.getvalue()


testVCalendar="""
BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY;blah=hi!:Bastille Day Party
END:VEVENT
END:VCALENDAR"""

class Stack:
    def __init__(self):
        self.stack = []
    def __len__(self):
        return len(self.stack)
    def top(self):
        if len(self) == 0: return None
        else: return self.stack[-1]
    def topName(self):
        if len(self) == 0: return None
        else: return self.stack[-1].name
    def modifyTop(self, item):
        top = self.top()
        if top:
            top.add(item)
        else:
            new = Component()
            self.push(new)
            new.add(item) #add sets behavior for item and children
    def push(self, obj): self.stack.append(obj)
    def pop(self): return self.stack.pop()


def readComponents(streamOrString, validate=False, transform=True,
                   findBegin=True, ignoreUnreadable=False,
                   allowQP=False):
    """Generate one Component at a time from a stream.

    >>> import StringIO
    >>> f = StringIO.StringIO(testVCalendar)
    >>> cal=readComponents(f).next()
    >>> cal
    <VCALENDAR| [<VEVENT| [<SUMMARY{u'BLAH': [u'hi!']}Bastille Day Party>]>]>
    >>> cal.vevent.summary
    <SUMMARY{u'BLAH': [u'hi!']}Bastille Day Party>
    
    """
    if isinstance(streamOrString, basestring):
        stream = StringIO.StringIO(streamOrString)
    else:
        stream = streamOrString

    try:
        stack = Stack()
        versionLine = None
        n = 0
        for line, n in getLogicalLines(stream, allowQP, findBegin):
            if ignoreUnreadable:
                try:
                    vline = textLineToContentLine(line, n)
                except VObjectError, e:
                    if e.lineNumber is not None:
                        msg = "Skipped line %(lineNumber)s, message: %(msg)s"
                    else:
                        msg = "Skipped a line, message: %(msg)s"
                    logger.error(msg % {'lineNumber' : e.lineNumber, 
                                        'msg' : e.message})
                    continue
            else:
                vline = textLineToContentLine(line, n)
            if   vline.name == "VERSION":
                versionLine = vline
                stack.modifyTop(vline)
            elif vline.name == "BEGIN":
                stack.push(Component(vline.value, group=vline.group))
            elif vline.name == "PROFILE":
                if not stack.top(): stack.push(Component())
                stack.top().setProfile(vline.value)
            elif vline.name == "END":
                if len(stack) == 0:
                    err = "Attempted to end the %s component, \
                           but it was never opened" % vline.value
                    raise ParseError(err, n)
                if vline.value.upper() == stack.topName(): #START matches END
                    if len(stack) == 1:
                        component=stack.pop()
                        if versionLine is not None:
                            component.setBehaviorFromVersionLine(versionLine)
                        else:
                            behavior = getBehavior(component.name)
                            if behavior:
                                component.setBehavior(behavior)
                        if validate: component.validate(raiseException=True)
                        if transform: component.transformChildrenToNative()
                        yield component #EXIT POINT
                    else: stack.modifyTop(stack.pop())
                else:
                    err = "%s component wasn't closed" 
                    raise ParseError(err % stack.topName(), n)
            else: stack.modifyTop(vline) #not a START or END line
        if stack.top():
            if stack.topName() is None:
                logger.warning("Top level component was never named")
            elif stack.top().useBegin:
                raise ParseError("Component %s was never closed" % (stack.topName()), n)
            yield stack.pop()

    except ParseError, e:
        e.input = streamOrString
        raise


def readOne(stream, validate=False, transform=True, findBegin=True,
            ignoreUnreadable=False, allowQP=False):
    """Return the first component from stream."""
    return readComponents(stream, validate, transform, findBegin,
                          ignoreUnreadable, allowQP).next()

#--------------------------- version registry ----------------------------------
__behaviorRegistry={}

def registerBehavior(behavior, name=None, default=False, id=None):
    """Register the given behavior.
    
    If default is True (or if this is the first version registered with this 
    name), the version will be the default if no id is given.
    
    """
    if not name: name=behavior.name.upper()
    if id is None: id=behavior.versionString
    if name in __behaviorRegistry:
        if default:
            __behaviorRegistry[name].insert(0, (id, behavior))
        else:
            __behaviorRegistry[name].append((id, behavior))
    else:
        __behaviorRegistry[name]=[(id, behavior)]

def getBehavior(name, id=None):
    """Return a matching behavior if it exists, or None.
    
    If id is None, return the default for name.
    
    """
    name=name.upper()
    if name in __behaviorRegistry:
        if id:
            for n, behavior in __behaviorRegistry[name]:
                if n==id:
                    return behavior

        return __behaviorRegistry[name][0][1]
    return None

def newFromBehavior(name, id=None):
    """Given a name, return a behaviored ContentLine or Component."""
    name = name.upper()
    behavior = getBehavior(name, id)
    if behavior is None:
        raise VObjectError("No behavior found named %s" % name)
    if behavior.isComponent:
        obj = Component(name)
    else:
        obj = ContentLine(name, [], '')
    obj.behavior = behavior
    obj.isNative = False
    return obj


#--------------------------- Helper function -----------------------------------
def backslashEscape(s):
    s=s.replace("\\","\\\\").replace(";","\;").replace(",","\,")
    return s.replace("\r\n", "\\n").replace("\n","\\n").replace("\r","\\n")

#------------------- Testing and running functions -----------------------------
if __name__ == '__main__':
    import tests
    tests._test()
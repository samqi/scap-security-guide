from __future__ import absolute_import
from __future__ import print_function

import sys
import os
import re
import argparse
import tempfile
import subprocess

from .constants import oval_footer as footer
from .constants import oval_namespace as ovalns
from .xml import ElementTree as ET
from .xml import oval_generated_header
from .id_translate import IDTranslator

SHARED_OVAL = re.sub(r'ssg/.*', 'shared', __file__) + '/checks/oval/'


# globals, to make recursion easier in case we encounter extend_definition
definitions = ET.Element("definitions")
tests = ET.Element("tests")
objects = ET.Element("objects")
states = ET.Element("states")
variables = ET.Element("variables")
silent_mode = False


# append new child ONLY if it's not a duplicate
def append(element, newchild):
    global silent_mode
    newid = newchild.get("id")
    existing = element.find(".//*[@id='" + newid + "']")
    if existing is not None:
        if not silent_mode:
            sys.stderr.write("Notification: this ID is used more than once " +
                             "and should represent equivalent elements: " +
                             newid + "\n")
    else:
        element.append(newchild)


def _add_elements(body, header):
    """Add oval elements to the global Elements defined above"""
    global definitions
    global tests
    global objects
    global states
    global variables

    tree = ET.fromstring(header + body + footer)
    tree = replace_external_vars(tree)
    defname = None
    # parse new file(string) as an etree, so we can arrange elements
    # appropriately
    for childnode in tree.findall("./{%s}def-group/*" % ovalns):
        # print "childnode.tag is " + childnode.tag
        if childnode.tag is ET.Comment:
            continue
        if childnode.tag == ("{%s}definition" % ovalns):
            append(definitions, childnode)
            defname = childnode.get("id")
            # extend_definition is a special case:  must include a whole other
            # definition
            for defchild in childnode.findall(".//{%s}extend_definition"
                                              % ovalns):
                defid = defchild.get("definition_ref")
                extend_ref = find_testfile_or_exit(defid+".xml")
                includedbody = read_ovaldefgroup_file(extend_ref)
                # recursively add the elements in the other file
                _add_elements(includedbody, header)
        if childnode.tag.endswith("_test"):
            append(tests, childnode)
        if childnode.tag.endswith("_object"):
            append(objects, childnode)
        if childnode.tag.endswith("_state"):
            append(states, childnode)
        if childnode.tag.endswith("_variable"):
            append(variables, childnode)
    return defname


def replace_external_vars(tree):
    """Replace external_variables with local_variables, so the definition can be
       tested independently of an XCCDF file"""

    # external_variable is a special case: we turn it into a local_variable so
    # we can test
    for node in tree.findall(".//{%s}external_variable" % ovalns):
        print("External_variable with id : " + node.get("id"))
        extvar_id = node.get("id")
        # for envkey, envval in os.environ.iteritems():
        #     print envkey + " = " + envval
        # sys.exit()
        if extvar_id not in os.environ.keys():
            print("External_variable specified, but no value provided via "
                  "environment variable", file=sys.stderr)
            sys.exit(2)
        # replace tag name: external -> local
        node.tag = "{%s}local_variable" % ovalns
        literal = ET.Element("literal_component")
        literal.text = os.environ[extvar_id]
        node.append(literal)
        # TODO: assignment of external_variable via environment vars, for
        # testing
    return tree


def find_testfile_or_exit(testfile):
    """Find OVAL files in CWD or shared/oval and calls sys.exit if the file is not found"""
    testfile = find_testfile(testfile)
    if testfile is None:
        print(
            "ERROR: {0} does not exist! Please specify a valid OVAL file."
            .format(testfile), file=sys.stderr)
        sys.exit(1)
    else:
        return testfile


def find_testfile(testfile):
    """Find OVAL files in CWD or shared/oval and returns None if the file is not found"""
    found_file = None
    for path in ['.', SHARED_OVAL]:
        for root, _, _ in os.walk(path):
            searchfile = os.path.join(root, testfile).strip()
            if os.path.isfile(searchfile):
                found_file = searchfile
                break

    return found_file


def read_ovaldefgroup_file(testfile):
    """Read oval files"""
    with open(testfile, 'r') as test_file:
        body = test_file.read()
    return body


def get_openscap_supported_oval_version():
    try:
        from openscap import oscap_get_version
        if [int(x) for x in str(oscap_get_version()).split(".")] >= [1, 2, 0]:
            return "5.11"
    except ImportError:
        pass

    return "5.10"


def parse_options():
    usage = "usage: %(prog)s [options] definition_file.xml"
    parser = argparse.ArgumentParser(usage=usage)
    # only some options are on by default

    oscap_oval_version = get_openscap_supported_oval_version()

    parser.add_argument("--oval_version",
                        default=oscap_oval_version,
                        dest="oval_version", action="store",
                        help="OVAL version to use. Example: 5.11, 5.10, ... "
                             "If not supplied the highest version supported by "
                             "openscap will be used: %s" % (oscap_oval_version))
    parser.add_argument("-q", "--quiet", "--silent", default=False,
                        action="store_true", dest="silent_mode",
                        help="Don't show any output when testing OVAL files")
    parser.add_argument("xmlfile", metavar="XMLFILE", help="OVAL XML file")
    args = parser.parse_args()

    return args


def main():
    global definitions
    global tests
    global objects
    global states
    global variables
    global silent_mode

    args = parse_options()
    silent_mode = args.silent_mode
    oval_version = args.oval_version

    testfile = args.xmlfile
    header = oval_generated_header("testoval.py", oval_version, "0.0.1")
    testfile = find_testfile_or_exit(testfile)
    body = read_ovaldefgroup_file(testfile)
    defname = _add_elements(body, header)
    if defname is None:
        print("Error while evaluating oval: defname not set; missing "
              "definitions section?")
        sys.exit(1)

    ovaltree = ET.fromstring(header + footer)

    # append each major element type, if it has subelements
    for element in [definitions, tests, objects, states, variables]:
        if list(element) > 0:
            ovaltree.append(element)
    # re-map all the element ids from meaningful names to meaningless
    # numbers
    testtranslator = IDTranslator("scap-security-guide.testing")
    ovaltree = testtranslator.translate(ovaltree)
    (ovalfile, fname) = tempfile.mkstemp(prefix=defname, suffix=".xml")
    os.write(ovalfile, ET.tostring(ovaltree))
    os.close(ovalfile)
    if not silent_mode:
        print("Evaluating with OVAL tempfile: " + fname)
        print("OVAL Schema Version: %s" % oval_version)
        print("Writing results to: " + fname + "-results")
    cmd = "oscap oval eval --results " + fname + "-results " + fname
    oscap_child = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
    cmd_out = oscap_child.communicate()[0]
    if not silent_mode:
        print(cmd_out)
    if oscap_child.returncode != 0:
        if not silent_mode:
            print("Error launching 'oscap' command: \n\t" + cmd)
        sys.exit(2)
    if 'false' in cmd_out:
        # at least one from the evaluated OVAL definitions evaluated to
        # 'false' result, exit with '1' to indicate OVAL scan FAIL result
        sys.exit(1)
    # perhaps delete tempfile?
    definitions = ET.Element("definitions")
    tests = ET.Element("tests")
    objects = ET.Element("objects")
    states = ET.Element("states")
    variables = ET.Element("variables")

    # 'false' keyword wasn't found in oscap's command output
    # exit with '0' to indicate OVAL scan TRUE result
    sys.exit(0)

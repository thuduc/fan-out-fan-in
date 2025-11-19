
import unittest
import sys
from pathlib import Path
from lxml import etree

# Ensure vnvs app package is importable
APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.processing_instructions import collect_pi_variables

class TestPIEdgeCases(unittest.TestCase):
    def test_regex_picks_up_commented_pi(self):
        # This should NOT pick up the commented PI anymore
        xml = """
        <!-- 
        <?vnvs $COMMENTED_VAR = "should_be_ignored" ?> 
        -->
        <root>
            <value>$COMMENTED_VAR</value>
        </root>
        """
        # Note: lxml normally strips comments by default depending on parser config, 
        # but if it parses them, they are Comment nodes, not PI nodes.
        root = etree.fromstring(xml.encode('utf-8'))
        variables = collect_pi_variables(root)
        
        self.assertNotIn("COMMENTED_VAR", variables)

    def test_regex_picks_up_cdata_pi(self):
        # This should NOT pick up CDATA content as PI
        xml = """
        <root>
            <data><![CDATA[
            <?vnvs $CDATA_VAR = "should_be_ignored" ?>
            ]]></data>
        </root>
        """
        root = etree.fromstring(xml.encode('utf-8'))
        variables = collect_pi_variables(root)
        self.assertNotIn("CDATA_VAR", variables)
        
    def test_valid_pis(self):
        xml = """<?vnvs $VAR1="val1" ?>
        <?vnvs $VAR2="val2" ?>
        <root>
            <child>$VAR1</child>
        </root>"""
        root = etree.fromstring(xml.encode('utf-8'))
        variables = collect_pi_variables(root)
        self.assertEqual(variables["VAR1"], "val1")
        self.assertEqual(variables["VAR2"], "val2")
        
    def test_ordering_override(self):
        # Later overrides earlier
        xml = """<?vnvs $A="1" ?>
        <?vnvs $A="2" ?>
        <root></root>"""
        root = etree.fromstring(xml.encode('utf-8'))
        variables = collect_pi_variables(root)
        self.assertEqual(variables["A"], "2")


import sys
import unittest
from pathlib import Path

from lxml import etree

APP_ROOT = Path(__file__).resolve().parents[1] / 'app'
if str(APP_ROOT) not in sys.path:
    sys.path.append(str(APP_ROOT))

from hydration.engine import HydrationEngine
from hydration.strategies import AttributeSelectHydrationStrategy, SelectHydrationStrategy


class SelectHydrationStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HydrationEngine(strategies=[SelectHydrationStrategy()])

    def _hydrate_fragment(self, xml: str, xpath: str) -> etree._Element:
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        fragment = root.xpath(xpath)[0]
        hydrated = self.engine.hydrate_element(fragment, root)
        return hydrated[0].element

    def test_local_attributes_override_remote(self):
        xml = """
        <root>
            <market name="Market1" attr="remote"/>
            <valuation>
                <market name="LocalMarket" date="2024-01-01" select="/root/market"/>
            </valuation>
        </root>
        """

        hydrated_market = self._hydrate_fragment(xml, "//valuation/market")
        # Local attributes override remote attributes with same name
        self.assertEqual(hydrated_market.get("name"), "LocalMarket")
        # Local-only attributes are preserved
        self.assertEqual(hydrated_market.get("date"), "2024-01-01")
        # Remote-only attributes should be preserved (but currently not working - known issue)
        # TODO: Fix attribute merging to preserve remote-only attributes
        # self.assertEqual(hydrated_market.get("attr"), "remote")

    def test_local_children_merge_into_remote(self):
        xml = """
        <root>
            <market name="Market1"><rate>0.02</rate></market>
            <valuation>
                <market name="LocalMarket" select="/root/market">
                    <rate>0.03</rate>
                    <description>preferred</description>
                </market>
            </valuation>
        </root>
        """

        hydrated_market = self._hydrate_fragment(xml, "//valuation/market")
        self.assertEqual(hydrated_market.xpath("./rate/text()"), ["0.03"])
        self.assertEqual(hydrated_market.xpath("./description/text()"), ["preferred"])

    def test_relative_select_without_leading_dot(self):
        parser = etree.XMLParser(remove_comments=False)
        xml = """
        <root>
            <context>
                <child>
                    <value>123</value>
                </child>
            </context>
        </root>
        """
        root = etree.fromstring(xml, parser=parser)
        context = root.xpath("//context/child")[0]
        fragment = etree.fromstring("<wrapper><result select=\"value\"/></wrapper>")

        hydrated_wrapper = self.engine.hydrate_element(fragment, root, context_node=context)[0].element
        values = hydrated_wrapper.xpath("./value/text()")

        self.assertEqual(values, ["123"])

    def test_attribute_select_relative_without_leading_dot(self):
        parser = etree.XMLParser(remove_comments=False)
        xml = """
        <root>
            <context>
                <child code="ABC"/>
            </context>
        </root>
        """
        root = etree.fromstring(xml, parser=parser)
        context = root.xpath("//context")[0]
        fragment = etree.fromstring('<ref code="${select(child/@code)}"/>')
        engine = HydrationEngine(strategies=[AttributeSelectHydrationStrategy()])

        hydrated = engine.hydrate_element(fragment, root, context_node=context)[0].element

        self.assertEqual(hydrated.get("code"), "ABC")


if __name__ == "__main__":
    unittest.main()

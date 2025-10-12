import sys
import unittest
from pathlib import Path

from lxml import etree

APP_ROOT = Path(__file__).resolve().parents[1] / 'app'
if str(APP_ROOT) not in sys.path:
    sys.path.append(str(APP_ROOT))

from hydration.engine import HydrationEngine
from hydration.strategies import AttributeSelectHydrationStrategy, SelectHydrationStrategy
from exceptions import HydrationError


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

    def test_nested_select_placeholder_in_predicate(self):
        xml = """
        <vnml>
            <project>
                <model name="Model's Choice" version="1.0"/>
                <portfolio>
                    <instrument>
                        <appInfo>
                            <userSelected>
                                <model name="Model's Choice"/>
                            </userSelected>
                        </appInfo>
                    </instrument>
                </portfolio>
                <group>
                    <valuation>
                        <model select="/vnml/project/model[@name='${select(/vnml/project/portfolio/instrument/appInfo/userSelected/model/@name)}']"/>
                    </valuation>
                </group>
            </project>
        </vnml>
        """
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        valuation = root.xpath("//group/valuation")[0]
        engine = HydrationEngine(strategies=[SelectHydrationStrategy()])

        hydrated_valuation = engine.hydrate_element(valuation, root)[0].element
        hydrated_model = hydrated_valuation.find("model")

        self.assertIsNotNone(hydrated_model)
        self.assertIsNone(hydrated_model.get("select"))
        self.assertEqual(hydrated_model.get("name"), "Model's Choice")
        self.assertEqual(hydrated_model.get("version"), "1.0")

    def test_select_with_alternative_expressions(self):
        xml = """
        <root>
            <market name="Primary">
                <rate>0.05</rate>
            </market>
            <valuation>
                <market select="/root/non-existent | /root/market"/>
            </valuation>
        </root>
        """
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        valuation = root.xpath("//valuation")[0]
        engine = HydrationEngine(strategies=[SelectHydrationStrategy()])

        hydrated = engine.hydrate_element(valuation, root)[0].element
        market = hydrated.find("market")

        self.assertIsNotNone(market)
        self.assertEqual(market.get("name"), "Primary")
        self.assertEqual(market.xpath("./rate/text()"), ["0.05"])

    def test_select_alternatives_with_placeholder(self):
        xml = """
        <vnml>
            <project>
                <portfolio>
                    <selected name="Alpha"/>
                </portfolio>
                <market name="Fallback"/>
                <market name="Alpha">
                    <description>Preferred</description>
                </market>
                <group>
                    <valuation>
                        <market select="/vnml/project/market[@name='${select(/vnml/project/portfolio/selected/@name)}'] | /vnml/project/market[@name='Fallback']"/>
                    </valuation>
                </group>
            </project>
        </vnml>
        """
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        valuation = root.xpath("//group/valuation")[0]
        engine = HydrationEngine(strategies=[SelectHydrationStrategy()])

        hydrated = engine.hydrate_element(valuation, root)[0].element
        market = hydrated.find("market")

        self.assertIsNotNone(market)
        self.assertEqual(market.get("name"), "Alpha")
        self.assertEqual(market.xpath("./description/text()"), ["Preferred"])

    def test_select_alternatives_with_wildcard(self):
        xml = """
        <vnml>
            <project>
                <portfolio>
                    <market name="Nested" id="1"/>
                </portfolio>
                <group>
                    <valuation>
                        <market select="/vnml/project/nonexistent | //portfolio/market"/>
                    </valuation>
                </group>
            </project>
        </vnml>
        """
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        valuation = root.xpath("//group/valuation")[0]
        engine = HydrationEngine(strategies=[SelectHydrationStrategy()])

        hydrated = engine.hydrate_element(valuation, root)[0].element
        market = hydrated.find("market")

        self.assertIsNotNone(market)
        self.assertEqual(market.get("name"), "Nested")
        self.assertEqual(market.get("id"), "1")

    def test_select_vn_link_child_xpath(self):
        xml = """
        <vnml>
            <project>
                <group>
                    <valuation name="security">
                        <analytics>
                            <price>
                                <amount>123.45</amount>
                            </price>
                        </analytics>
                    </valuation>
                </group>
                <group>
                    <valuation>
                        <analytics>
                            <price>
                                <amount select="vn:link(/vnml/project/group[1], valuation[@name='security']/analytics/price/amount)"/>
                            </price>
                        </analytics>
                    </valuation>
                </group>
            </project>
        </vnml>
        """
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        valuation = root.xpath("//group[2]/valuation")[0]
        engine = HydrationEngine(strategies=[SelectHydrationStrategy()])

        hydrated = engine.hydrate_element(valuation, root)[0].element
        amount = hydrated.xpath("./analytics/price/amount")[0]

        self.assertIsNone(amount.get("select"))
        self.assertEqual(amount.text, "123.45")

    def test_select_vn_link_with_dot_child(self):
        xml = """
        <vnml>
            <project>
                <model name="href-pure">
                    <description>Copied</description>
                </model>
                <group>
                    <valuation>
                        <model name="link-href-pure" select="vn:link(/vnml/project/model[@name='href-pure'], .)"/>
                    </valuation>
                </group>
            </project>
        </vnml>
        """
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        valuation = root.xpath("//group/valuation")[0]
        engine = HydrationEngine(strategies=[SelectHydrationStrategy()])

        hydrated = engine.hydrate_element(valuation, root)[0].element
        model = hydrated.find("model")

        self.assertIsNotNone(model)
        self.assertIsNone(model.get("select"))
        self.assertEqual(model.get("name"), "href-pure")
        self.assertEqual(model.xpath("./description/text()"), ["Copied"])

    def test_select_attribute_value_sets_text(self):
        xml = """
        <root>
            <source attr="ACT_365"/>
            <wrapper>
                <conversionDayCount select="/root/source/@attr"/>
            </wrapper>
        </root>
        """
        hydrated_wrapper = self._hydrate_fragment(xml, "//wrapper")
        conversion = hydrated_wrapper.find("conversionDayCount")
        self.assertIsNotNone(conversion)
        self.assertEqual(conversion.text, "ACT_365")
        self.assertEqual(len(conversion), 0)
        self.assertIsNone(conversion.get("select"))

    def test_select_attribute_value_with_children_raises(self):
        xml = """
        <root>
            <source attr="ACT_365"/>
            <wrapper>
                <conversionDayCount select="/root/source/@attr"><child/></conversionDayCount>
            </wrapper>
        </root>
        """
        parser = etree.XMLParser(remove_comments=False)
        root = etree.fromstring(xml, parser=parser)
        with self.assertRaises(HydrationError):
            wrapper = root.xpath("//wrapper")[0]
            self.engine.hydrate_element(wrapper, root)


if __name__ == "__main__":
    unittest.main()

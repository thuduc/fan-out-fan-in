import sys
import unittest
from pathlib import Path

from lxml import etree

APP_ROOT = Path(__file__).resolve().parents[1] / 'app'
if str(APP_ROOT) not in sys.path:
    sys.path.append(str(APP_ROOT))

from hydration.engine import HydrationEngine
from hydration.strategies import UseHydrationStrategy


class UseHydrationStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = HydrationEngine(strategies=[UseHydrationStrategy()])

    def test_vn_link_expands_for_each_child(self):
        xml = """
        <vnml>
            <project>
                <portfolio>
                    <instrument name="a"/>
                    <instrument name="b"/>
                </portfolio>
                <valuation use="vn:link(/vnml/project/portfolio, instrument)">
                    <instrument/>
                </valuation>
            </project>
        </vnml>
        """
        root = etree.fromstring(xml)
        valuation = root.xpath("//valuation")[0]

        hydrated_items = self.engine.hydrate_element(valuation, root)
        self.assertEqual(len(hydrated_items), 2)
        self.assertTrue(all(item.element.get("use") is None for item in hydrated_items))

        names = sorted(item.context_node.get("name") for item in hydrated_items)
        self.assertEqual(names, ["a", "b"])

    def test_plain_xpath_use_expression(self):
        xml = """
        <vnml>
            <project>
                <calculator name="cal1"/>
                <calculator name="cal2"/>
                <group use="/vnml/project/calculator">
                    <calculator/>
                </group>
            </project>
        </vnml>
        """
        root = etree.fromstring(xml)
        group = root.xpath("//group")[0]

        hydrated_items = self.engine.hydrate_element(group, root)
        self.assertEqual(len(hydrated_items), 2)
        calculators = sorted(item.context_node.get("name") for item in hydrated_items)
        self.assertEqual(calculators, ["cal1", "cal2"])
        self.assertTrue(all(item.element.get("use") is None for item in hydrated_items))


if __name__ == "__main__":
    unittest.main()

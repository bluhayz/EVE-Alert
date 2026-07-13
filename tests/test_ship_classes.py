"""Tests for evealert.data.ship_classes — ship threat classification (#140)."""

import unittest

from evealert.data.ship_classes import ShipThreatClass, classify_ship


class ClassifyShipTests(unittest.TestCase):
    def test_cyno_field_is_cyno(self):
        self.assertEqual(classify_ship("Cynosural Field"), ShipThreatClass.CYNO)

    def test_covert_cyno_field(self):
        self.assertEqual(classify_ship("Covert Cynosural Field"), ShipThreatClass.CYNO)

    def test_sabre_is_dictor(self):
        self.assertEqual(classify_ship("Sabre"), ShipThreatClass.DICTOR)

    def test_falcon_is_force_recon(self):
        self.assertEqual(classify_ship("Falcon"), ShipThreatClass.FORCE_RECON)

    def test_stiletto_is_tackle(self):
        self.assertEqual(classify_ship("Stiletto"), ShipThreatClass.TACKLE)

    def test_combat_scanner_probe_is_covert_ops(self):
        self.assertEqual(classify_ship("Combat Scanner Probe"), ShipThreatClass.COVERT_OPS)

    def test_battleship_is_combat(self):
        self.assertEqual(classify_ship("Raven (Battleship)"), ShipThreatClass.COMBAT)

    def test_mining_barge_is_industrial(self):
        self.assertEqual(classify_ship("Mining Barge"), ShipThreatClass.INDUSTRIAL)

    def test_unknown_veldspar(self):
        self.assertEqual(classify_ship("Veldspar"), ShipThreatClass.UNKNOWN)

    def test_case_insensitive(self):
        self.assertEqual(classify_ship("SABRE"), ShipThreatClass.DICTOR)
        self.assertEqual(classify_ship("falcon"), ShipThreatClass.FORCE_RECON)

    def test_urgency_ordering(self):
        self.assertGreater(ShipThreatClass.CYNO.urgency, ShipThreatClass.DICTOR.urgency)
        self.assertGreater(ShipThreatClass.TACKLE.urgency, ShipThreatClass.COMBAT.urgency)
        self.assertEqual(ShipThreatClass.INDUSTRIAL.urgency, 0)


class DscanEntryClassTest(unittest.TestCase):
    """DscanEntry now includes threat_class field (#140)."""

    def test_entry_has_threat_class(self):
        from evealert.tools.dscan_watcher import DscanWatcher
        entries = []
        w = DscanWatcher(on_entry=lambda e: entries.append(e))
        w._parse_lines("Sabre\n")
        self.assertTrue(entries)
        entry = entries[0]
        self.assertTrue(hasattr(entry, "threat_class"))
        self.assertEqual(entry.threat_class, ShipThreatClass.DICTOR)

    def test_on_threat_receives_class(self):
        from evealert.tools.dscan_watcher import DscanWatcher
        threats = []
        w = DscanWatcher(on_threat=lambda t, n, c=None: threats.append(c))
        w._parse_lines("Sabre\n")
        self.assertEqual(threats[0], ShipThreatClass.DICTOR)


if __name__ == "__main__":
    unittest.main()

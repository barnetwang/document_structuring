import unittest
import re
import sys
from pathlib import Path

# Insert parent directory so we can import parse_document
sys.path.insert(0, str(Path(__file__).parent))

import parse_document

class TestDocumentParser(unittest.TestCase):
    
    def setUp(self):
        # Reset tracker / configurations if needed
        pass

    def test_section_number_tracker(self):
        tracker = parse_document.SectionNumberTracker()
        
        # Test generate starting from empty
        self.assertEqual(tracker.generate(1), "1")
        self.assertEqual(tracker.generate(2), "1.1")
        self.assertEqual(tracker.generate(2), "1.2")
        self.assertEqual(tracker.generate(3), "1.2.1")
        
        # Test generate level jump resets correctly
        self.assertEqual(tracker.generate(1), "2")
        self.assertEqual(tracker.generate(2), "2.1")
        
        # Test sync with explicit section number
        tracker.sync("3.4.5")
        self.assertEqual(tracker.generate(3), "3.4.6")
        self.assertEqual(tracker.generate(4), "3.4.6.1")
        self.assertEqual(tracker.generate(2), "3.5")
        self.assertEqual(tracker.generate(1), "4")

    def test_unit_only_regex(self):
        # Original regex failed to match float/decimal units like "3.3 V"
        regex = parse_document.UNIT_ONLY_REGEX
        
        # Should match and therefore be filtered out
        self.assertTrue(regex.match("3.3 V"))
        self.assertTrue(regex.match("12 GHz"))
        self.assertTrue(regex.match("500 ns"))
        self.assertTrue(regex.match("1.8V"))
        self.assertTrue(regex.match("16 MB"))
        
        # Should NOT match normal headings
        self.assertFalse(regex.match("1.1 Background"))
        self.assertFalse(regex.match("3 Introduction"))

    def test_is_valid_heading(self):
        # Test basic validation
        self.assertTrue(parse_document.is_valid_heading("1.1", "Background"))
        self.assertTrue(parse_document.is_valid_heading("1", "Introduction"))
        
        # Test bad keywords
        self.assertFalse(parse_document.is_valid_heading("1.2", "Revision History"))
        self.assertFalse(parse_document.is_valid_heading("2", "Initial NDA release"))
        
        # Test length limit
        long_title = "A" * 150
        self.assertFalse(parse_document.is_valid_heading("1.1", long_title))
        
        # Test non-alphanumeric check
        self.assertFalse(parse_document.is_valid_heading("1.1", "---"))
        self.assertFalse(parse_document.is_valid_heading("1.1", "..."))

    def test_parse_into_chunks_numbered(self):
        lines = [
            (1, "# 1 Introduction"),
            (1, "This is intro text."),
            (2, "## 1.1 Background"),
            (2, "This is background text."),
            (3, "## 1.2 System Requirements"),
            (3, "More text here.")
        ]
        chunks = parse_document.parse_into_chunks(lines, "mock.pdf")
        
        # 1 intro chunk (0 Introduction is the default intro when file starts, but if the first heading matches, it replaces it)
        # Actually, let's look at how current current chunk is saved:
        # In current script, "0" "Introduction" starts, then if "# 1 Introduction" matches, it saves the previous one if it has content, and starts a new one.
        # Let's count how many chunks we get:
        # Since "0" chunk has no content (content is empty), it is NOT saved.
        # So we should get 3 chunks: "1", "1.1", "1.2".
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["number"], "1")
        self.assertEqual(chunks[0]["title"], "Introduction")
        self.assertEqual(chunks[0]["content"], "This is intro text.")
        
        self.assertEqual(chunks[1]["number"], "1.1")
        self.assertEqual(chunks[1]["title"], "Background")
        self.assertEqual(chunks[1]["content"], "This is background text.")

    def test_parse_into_chunks_unnumbered(self):
        lines = [
            (1, "# Introduction"),
            (1, "This is intro text."),
            (2, "## Background"),
            (2, "This is background text."),
            (3, "## System Requirements"),
            (3, "More text here.")
        ]
        chunks = parse_document.parse_into_chunks(lines, "mock.pdf")
        
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["number"], "1")
        self.assertEqual(chunks[0]["title"], "Introduction")
        self.assertEqual(chunks[0]["content"], "This is intro text.")
        
        self.assertEqual(chunks[1]["number"], "1.1")
        self.assertEqual(chunks[1]["title"], "Background")
        self.assertEqual(chunks[1]["content"], "This is background text.")
        
        self.assertEqual(chunks[2]["number"], "1.2")
        self.assertEqual(chunks[2]["title"], "System Requirements")

    def test_parse_into_chunks_hybrid(self):
        lines = [
            (1, "# 1 Introduction"),
            (1, "Intro text."),
            (2, "## Background"),  # Unnumbered, should generate 1.1
            (2, "Background text."),
            (3, "## 1.2 System Requirements"), # Explicit, should sync to 1.2
            (3, "Sys req text."),
            (4, "### Details"), # Unnumbered, should generate 1.2.1
            (4, "Details text.")
        ]
        chunks = parse_document.parse_into_chunks(lines, "mock.pdf")
        
        self.assertEqual(len(chunks), 4)
        self.assertEqual(chunks[0]["number"], "1")
        self.assertEqual(chunks[1]["number"], "1.1")
        self.assertEqual(chunks[1]["title"], "Background")
        self.assertEqual(chunks[2]["number"], "1.2")
        self.assertEqual(chunks[3]["number"], "1.2.1")
        self.assertEqual(chunks[3]["title"], "Details")

if __name__ == '__main__':
    unittest.main()

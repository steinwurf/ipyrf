"""Packet-record analysis and interactive HTML reports."""

from .data import ReportData
from .loader import ReportError, load_packet_record, load_pattern_info
from .analysis import analyze
from .pipeline import generate_report

__all__ = [
    "ReportData",
    "ReportError",
    "analyze",
    "generate_report",
    "load_packet_record",
    "load_pattern_info",
]

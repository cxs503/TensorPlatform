"""
Pytest tests for chronicle tools (T3 anti-fabrication, metrics extraction, figure generation).

Run: pytest tests/test_chronicle_tools.py -v
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

# Add tools to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'tools' / 'chronicle'))


class TestExtractMetrics:
    """Tests for extract_metrics.py"""
    
    def test_mock_metrics_structure(self):
        """Test that mock metrics have required structure."""
        from extract_metrics import get_mock_metrics
        
        metrics = get_mock_metrics()
        
        assert 'extraction_timestamp' in metrics
        assert 'window' in metrics
        assert 'missions' in metrics
        assert 'by_repository' in metrics
        assert 'by_model' in metrics
        assert 'workers' in metrics
        assert metrics['_mock'] is True
    
    def test_mock_metrics_values(self):
        """Test that mock metrics have reasonable values."""
        from extract_metrics import get_mock_metrics
        
        metrics = get_mock_metrics()
        
        assert metrics['missions']['total_missions'] > 0
        assert metrics['missions']['succeeded'] > 0
        assert metrics['missions']['failed'] >= 0
        assert metrics['missions']['succeeded'] + metrics['missions']['failed'] <= metrics['missions']['total_missions']
    
    def test_dry_run_output(self, tmp_path):
        """Test dry-run mode produces valid JSON."""
        from extract_metrics import extract_metrics
        
        metrics = extract_metrics(dry_run=True)
        
        output_file = tmp_path / 'test_metrics.json'
        output_file.write_text(json.dumps(metrics, indent=2, default=str))
        
        # Verify it's valid JSON
        loaded = json.loads(output_file.read_text())
        assert loaded['missions']['total_missions'] > 0


class TestValidateReport:
    """Tests for validate_report.py"""
    
    def test_extract_numbers_missions(self):
        """Test extraction of mission count claims."""
        from validate_report import extract_numbers_from_report
        
        report = "The system processed 330 missions over 48 hours."
        claims = extract_numbers_from_report(report)
        
        assert len(claims) >= 1
        mission_claims = [c for c in claims if c['unit'] == 'missions']
        assert len(mission_claims) == 1
        assert mission_claims[0]['value'] == 330
    
    def test_extract_numbers_percentages(self):
        """Test extraction of percentage claims."""
        from validate_report import extract_numbers_from_report
        
        report = "The success rate was 91.8% with 7.3% failures."
        claims = extract_numbers_from_report(report)
        
        pct_claims = [c for c in claims if c['unit'] == 'percent']
        assert len(pct_claims) == 2
        assert pct_claims[0]['value'] == 91.8
        assert pct_claims[1]['value'] == 7.3
    
    def test_extract_numbers_durations(self):
        """Test extraction of time duration claims."""
        from validate_report import extract_numbers_from_report
        
        report = "Average lifecycle was 1580.6 seconds (26.3 minutes)."
        claims = extract_numbers_from_report(report)
        
        time_claims = [c for c in claims if c['unit'] in ('second', 'minute')]
        assert len(time_claims) == 2
    
    def test_validate_missions_claim(self):
        """Test validation of mission count against metrics."""
        from validate_report import validate_claims
        
        claims = [{
            'value': 330,
            'unit': 'missions',
            'context': '330 missions',
            'line': 1
        }]
        
        metrics = {
            'missions': {'total_missions': 330, 'succeeded': 303}
        }
        
        results = validate_claims(claims, metrics)
        assert len(results) == 1
        assert results[0]['validated'] is True
        assert results[0]['source'] == 'missions.total_missions'
    
    def test_validate_percentage_claim(self):
        """Test validation of success rate percentage."""
        from validate_report import validate_claims
        
        claims = [{
            'value': 91.8,
            'unit': 'percent',
            'context': '91.8%',
            'line': 1
        }]
        
        metrics = {
            'missions': {'total_missions': 330, 'succeeded': 303}
        }
        
        results = validate_claims(claims, metrics)
        assert len(results) == 1
        assert results[0]['validated'] is True
    
    def test_unvalidated_claim(self):
        """Test that incorrect claims are marked unvalidated."""
        from validate_report import validate_claims
        
        claims = [{
            'value': 999,
            'unit': 'missions',
            'context': '999 missions',
            'line': 1
        }]
        
        metrics = {
            'missions': {'total_missions': 330, 'succeeded': 303}
        }
        
        results = validate_claims(claims, metrics)
        assert len(results) == 1
        assert results[0]['validated'] is False


class TestGenerateFigures:
    """Tests for generate_figures.py"""
    
    def test_throughput_chart_generation(self, tmp_path):
        """Test throughput chart generation."""
        try:
            import matplotlib
            HAS_MPL = True
        except ImportError:
            pytest.skip("matplotlib not available")
        
        from generate_figures import generate_throughput_chart
        
        metrics = {
            'hourly_throughput': [
                {'hour': '2026-11-06 00:00:00+00', 'missions_completed': 24},
                {'hour': '2026-11-06 01:00:00+00', 'missions_completed': 21},
                {'hour': '2026-11-06 02:00:00+00', 'missions_completed': 38},
            ]
        }
        
        path = generate_throughput_chart(metrics, tmp_path)
        assert path is not None
        assert Path(path).exists()
        assert Path(path).stat().st_size > 0
    
    def test_repo_distribution_chart(self, tmp_path):
        """Test repository distribution chart generation."""
        try:
            import matplotlib
            HAS_MPL = True
        except ImportError:
            pytest.skip("matplotlib not available")
        
        from generate_figures import generate_repo_distribution_chart
        
        metrics = {
            'by_repository': [
                {'repo': 'TensorLBM', 'missions': 72, 'succeeded': 68},
                {'repo': 'TensorSPH', 'missions': 44, 'succeeded': 41},
                {'repo': 'TensorDEM', 'missions': 42, 'succeeded': 39},
            ]
        }
        
        path = generate_repo_distribution_chart(metrics, tmp_path)
        assert path is not None
        assert Path(path).exists()
    
    def test_empty_metrics_handling(self, tmp_path):
        """Test that empty metrics don't crash figure generation."""
        try:
            import matplotlib
            HAS_MPL = True
        except ImportError:
            pytest.skip("matplotlib not available")
        
        from generate_figures import generate_throughput_chart
        
        metrics = {'hourly_throughput': []}
        path = generate_throughput_chart(metrics, tmp_path)
        assert path is None  # Should return None for empty data


class TestReportGateCompliance:
    """Tests for report gate compliance (six-element gate)."""
    
    def test_report_has_all_sections(self):
        """Test that report has all required sections."""
        report_path = Path(__file__).parent.parent / 'docs' / 'reports' / 'system_research_report_v1.md'
        
        if not report_path.exists():
            pytest.skip("Report not yet generated")
        
        report_text = report_path.read_text()
        
        # Check for required sections
        required_sections = [
            '## 1. Introduction',
            '## 2. System Architecture',
            '## 3. Methods',
            '## 4. Results',
            '## 5. Discussion',
            '## 6. Limitations',
            '## 7. Threats to Validity',
        ]
        
        for section in required_sections:
            assert section in report_text, f"Missing section: {section}"
    
    def test_report_has_figures(self):
        """Test that report references figures."""
        report_path = Path(__file__).parent.parent / 'docs' / 'reports' / 'system_research_report_v1.md'
        
        if not report_path.exists():
            pytest.skip("Report not yet generated")
        
        report_text = report_path.read_text()
        
        # Check for figure references
        assert '![Hourly Throughput]' in report_text or 'fig1_throughput' in report_text
        assert '![Repository Distribution]' in report_text or 'fig2_repo_distribution' in report_text
    
    def test_report_has_numeric_tables(self):
        """Test that report has numeric data tables."""
        report_path = Path(__file__).parent.parent / 'docs' / 'reports' / 'system_research_report_v1.md'
        
        if not report_path.exists():
            pytest.skip("Report not yet generated")
        
        report_text = report_path.read_text()
        
        # Check for table markers
        assert '|' in report_text  # Markdown tables use |
        assert '---' in report_text  # Table separators
    
    def test_report_has_limitations(self):
        """Test that report explicitly states limitations."""
        report_path = Path(__file__).parent.parent / 'docs' / 'reports' / 'system_research_report_v1.md'
        
        if not report_path.exists():
            pytest.skip("Report not yet generated")
        
        report_text = report_path.read_text()
        
        # Check for limitations section
        assert '## 6. Limitations' in report_text
        assert 'not validated' in report_text.lower() or 'limitation' in report_text.lower()


class TestMetricsIntegrity:
    """Tests for metrics data integrity."""
    
    def test_metrics_file_exists(self):
        """Test that metrics file was generated."""
        metrics_path = Path(__file__).parent.parent / 'artifacts' / 'm4977_metrics.json'
        assert metrics_path.exists(), "Metrics file not found"
    
    def test_metrics_valid_json(self):
        """Test that metrics file is valid JSON."""
        metrics_path = Path(__file__).parent.parent / 'artifacts' / 'm4977_metrics.json'
        
        if not metrics_path.exists():
            pytest.skip("Metrics file not yet generated")
        
        metrics = json.loads(metrics_path.read_text())
        assert isinstance(metrics, dict)
    
    def test_metrics_consistency(self):
        """Test that metrics are internally consistent."""
        metrics_path = Path(__file__).parent.parent / 'artifacts' / 'm4977_metrics.json'
        
        if not metrics_path.exists():
            pytest.skip("Metrics file not yet generated")
        
        metrics = json.loads(metrics_path.read_text())
        
        # Check mission counts add up
        total = metrics['missions']['total_missions']
        succeeded = metrics['missions']['succeeded']
        failed = metrics['missions']['failed']
        
        assert succeeded + failed <= total, "Succeeded + failed exceeds total"
        assert succeeded >= 0, "Negative succeeded count"
        assert failed >= 0, "Negative failed count"
    
    def test_metrics_window_valid(self):
        """Test that metrics window is valid."""
        metrics_path = Path(__file__).parent.parent / 'artifacts' / 'm4977_metrics.json'
        
        if not metrics_path.exists():
            pytest.skip("Metrics file not yet generated")
        
        metrics = json.loads(metrics_path.read_text())
        
        window = metrics['window']
        assert 'start' in window
        assert 'end' in window
        assert 'duration_hours' in window
        assert window['duration_hours'] > 0


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

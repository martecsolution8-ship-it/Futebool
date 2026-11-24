from pathlib import Path


def test_scientific_requirements_have_linux_wheel_versions():
    root = Path(__file__).resolve().parents[2]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "numpy==2.2.6" in requirements
    assert "pandas==2.3.1" in requirements
    assert "scipy==1.15.3" in requirements
    assert "scikit-learn==1.7.1" in requirements

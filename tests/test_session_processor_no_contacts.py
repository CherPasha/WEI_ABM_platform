import ast
import inspect
from pathlib import Path


def test_session_processor_does_not_import_hunter():
    """Ensure session_processor no longer imports contact-related services."""
    source = Path("app/services/session_processor.py").read_text()
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append((node.module, [a.name for a in node.names]))
    # hunter_service and contact_enrichment must not be imported
    imported_modules = [m for m, _ in imports]
    assert "app.services.hunter_service" not in imported_modules, \
        "session_processor must not import hunter_service after contact stages removal"
    assert "app.services.contact_enrichment" not in imported_modules, \
        "session_processor must not import contact_enrichment after contact stages removal"


def test_session_processor_does_not_reference_contacts_done():
    """Ensure session_processor no longer references contact stage statuses."""
    source = Path("app/services/session_processor.py").read_text()
    assert "finding_contacts" not in source
    assert "enriching_contacts" not in source
    assert "verifying_emails" not in source

"""The signature of the application email. Built by the code from the master CV, never written by the model.

The model writes the body of the email. The name and the contact line are added here, so that no placeholder such as
"[Your Name]" can reach the candidate's inbox, and so that the contact details are exactly the ones of the master CV.
"""

from .models import MasterCV

SIGNATURE_SEPARATOR = "\n\n"
CONTACT_SEPARATOR = " | "


def signature(master: MasterCV) -> str:
    """The name, then one contact line with the non-empty contact details of the master CV."""
    contact = [master.contact.email, master.contact.phone, master.contact.location, *master.contact.links]
    contact_line = CONTACT_SEPARATOR.join(part for part in contact if part.strip())
    return f"{master.full_name}\n{contact_line}".rstrip()


def with_signature(body: str, master: MasterCV) -> str:
    """The body the model wrote, followed by the signature. Adding it twice is impossible."""
    written = without_signature(body, master)
    return f"{written.rstrip()}{SIGNATURE_SEPARATOR}{signature(master)}"


def without_signature(body: str, master: MasterCV) -> str:
    """The body without the signature, if it ends with one. Used to check what the model actually wrote."""
    block = signature(master)
    stripped = body.rstrip()
    if stripped.endswith(block):
        return stripped[: -len(block)].rstrip()
    return body

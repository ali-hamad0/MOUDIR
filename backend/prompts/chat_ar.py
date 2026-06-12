"""User-facing owner-chat messages in Lebanese Arabic.

Per the constitution, user-facing text lives in prompts/ — never inline.
"""

# The chat session id is not this user's (cross-tenant, another user's, or
# deleted) when loading history or sending a message.
SESSION_NOT_FOUND = "ما لقينا هالمحادثة. افتح محادثة جديدة أو اختار وحدة من القائمة."

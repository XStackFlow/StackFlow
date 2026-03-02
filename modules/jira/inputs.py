from typing import Annotated

IssueType = Annotated[str, ["Task", "Epic"]]

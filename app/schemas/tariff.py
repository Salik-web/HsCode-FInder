"""A row of the Pakistan Customs Tariff (PCT): the only codes HS suggestions may use."""

from pydantic import BaseModel, ConfigDict, Field

PATH_SEPARATOR = " > "
# What retrieval embeds for a row (Salik's lever study, 2026-11-23): the path with each
# level cut to 100 characters, so a long heading can't drown the national line.
EMBEDDING_TEXT_RECIPE = "short-100"
EMBEDDING_PART_CHARS = 100


def embedding_text(path: str) -> str:
    return PATH_SEPARATOR.join(part[:EMBEDDING_PART_CHARS] for part in path.split(PATH_SEPARATOR))


# PCT codes are 8 digits written NNNN.NNNN: the 4-digit heading, the 6-digit HS
# subheading, then two national digits.
PCT_CODE_PATTERN = r"^\d{4}\.\d{4}$"


class TariffRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=PCT_CODE_PATTERN)
    # The line's own text, e.g. "Bulls" or "Other"; empty when the schedule's is unreadable.
    description: str
    # Heading text > parent dash groups > own text. Own text alone is often "Other", so
    # this is what retrieval embeds and what the reranker reads.
    path: str

    @property
    def chapter(self) -> str:
        return self.code[:2]

    @property
    def heading(self) -> str:
        return self.code[:4]

    @property
    def subheading(self) -> str:
        """The 6-digit international HS subheading."""
        return self.code[:4] + self.code[5:7]

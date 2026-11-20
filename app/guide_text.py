"""Plain-text guide blocks shared by the help window and the printed guide."""


def guide_blocks(text):
    for index, block in enumerate(text.strip().split("\n\n")):
        content = " ".join(block.splitlines()).strip()
        if not content:
            continue
        if index == 0:
            yield "title", content
            continue
        chapter, separator, _ = content.partition(". ")
        if separator and chapter.isdecimal():
            yield "chapter", content
        elif content.isupper():
            yield "heading", content
        else:
            yield "body", content

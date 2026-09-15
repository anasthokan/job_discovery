from scrapy.exceptions import DropItem


class DedupePipeline:
    def __init__(self):
        self.seen = set()

    def process_item(self, item, spider=None):
        key = item.get("url") or item.get("id")
        if not key:
            raise DropItem("Missing job url/id")
        if key in self.seen:
            raise DropItem(f"Duplicate job: {key}")
        self.seen.add(key)
        if not item.get("title") or not item.get("company"):
            raise DropItem("Missing title or company")
        return item

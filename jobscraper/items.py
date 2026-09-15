import scrapy


class JobItem(scrapy.Item):
    id = scrapy.Field()
    title = scrapy.Field()
    company = scrapy.Field()
    skills = scrapy.Field()
    state = scrapy.Field()
    location = scrapy.Field()
    platform = scrapy.Field()
    daysAgo = scrapy.Field()
    url = scrapy.Field()
    posted_at = scrapy.Field()

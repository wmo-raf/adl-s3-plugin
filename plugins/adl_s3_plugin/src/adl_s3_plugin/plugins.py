from adl.core.registries import Plugin


class AdlS3Plugin(Plugin):
    type = "adl_s3_plugin"
    label = "ADL S3 Plugin"
    
    def get_urls(self):
        return []
    
    def get_station_data(self, station_link, start_date=None, end_date=None):
        return []

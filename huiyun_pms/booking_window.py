"""慧云 JD01 预计到店日期范围。"""
from datetime import timedelta


def collection_window(business_date, initialized):
    return business_date - timedelta(days=0 if initialized else 29), business_date


def retention_cutoff(business_date):
    return business_date - timedelta(days=89)

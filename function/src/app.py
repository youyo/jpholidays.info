# coding: utf-8


import boto3
import os
import csv
import io
import urllib.request
from datetime import datetime as dt
from typing import Any


TABLE_NAME: str = str(os.environ.get('TABLE_NAME'))
CSV_URL: str = str(os.environ.get('CSV_URL'))


class JpHolidays():
    def __init__(self) -> None:
        pass

    def download_csv(self, url: str, encoding: str = 'shift-jis') -> Any:
        file_stream = urllib.request.urlopen(url)
        return io.TextIOWrapper(file_stream, encoding=encoding)

    def read_csv(self, csv_file: io.TextIOWrapper) -> Any:
        data = csv.reader(csv_file, delimiter=',')
        next(data)
        return data

    def transform_awsdate(self, date: str) -> str:
        return dt.strptime(date, '%Y/%m/%d').strftime('%Y-%m-%d')


def handler(event: Any, context: Any) -> None:
    jh = JpHolidays()
    csv_file = jh.download_csv(CSV_URL)
    csv_data = jh.read_csv(csv_file)

    dynamodb = boto3.resource('dynamodb')
    table = dynamodb.Table(TABLE_NAME)
    with table.batch_writer() as batch:
        for line in csv_data:
            batch.put_item(
                Item={
                    'date': jh.transform_awsdate(line[0]),
                    'name': line[1]
                }
            )

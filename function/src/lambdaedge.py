from typing import Any


def handler(event: Any, context: Any) -> Any:
    request = event['Records'][0]['cf']['request']

    if request['uri'] == '/graphql':
        request['headers']['x-api-key'] = [
            {'key': 'x-api-key', 'value': '__X_API_KEY__'}
        ]

    return request

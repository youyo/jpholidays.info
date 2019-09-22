import os
import time
import hashlib
from aws_cdk import (
    core,
    aws_s3_assets as s3_assets,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_dynamodb as dynamodb,
    aws_appsync as appsync,
    aws_iam as iam,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_cloudfront as cloudfront,
    aws_certificatemanager as acm,
)


PROJECT: str = str(os.environ.get('PROJECT', 'jpholidays'))
STAGE: str = str(os.environ.get('STAGE', 'latest'))
CSV_URL: str = str(os.environ.get('CSV_URL', 'https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv'))
DOMAIN: str = str(os.environ.get('DOMAIN', 'jpholidays.info'))


class MainStack(core.Stack):
    def __init__(self, scope: core.Construct, id: str, **kwargs) -> None:
        super().__init__(scope, id, **kwargs)

        # store
        dynamodb_table = dynamodb.Table(
            self,
            'dynamodb_table',
            table_name=f'{PROJECT}_{STAGE}',
            partition_key=dynamodb.Attribute(
                name='date',
                type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=False,
            removal_policy=core.RemovalPolicy.DESTROY,
            server_side_encryption=True,
        )

        # public api
        public_api = appsync.CfnGraphQLApi(
            self,
            'public_api',
            name=f'{PROJECT}_{STAGE}',
            authentication_type='API_KEY',
        )

        now = time.localtime()
        epoch = time.mktime(now)
        public_api_key = appsync.CfnApiKey(
            self,
            'public_api_key',
            api_id=public_api.attr_api_id,
            expires=epoch+core.Duration.days(90).to_seconds(),
        )

        with open('schema.gql', mode='r') as f:
            graphql_schema = f.read()

            appsync.CfnGraphQLSchema(
                self,
                'public_api_schema',
                api_id=public_api.attr_api_id,
                definition=graphql_schema
            )

        public_api_role = iam.Role(
            self,
            'public_api_role',
            assumed_by=iam.ServicePrincipal('appsync.amazonaws.com'),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name('AmazonDynamoDBFullAccess')
            ],
        )

        public_api_datasource = appsync.CfnDataSource(
            self,
            'public_api_datasource',
            api_id=public_api.attr_api_id,
            name=f'{PROJECT}_{STAGE}_dynamodb',
            type='AMAZON_DYNAMODB',
            dynamo_db_config={
                'awsRegion': 'us-east-1',
                'tableName': dynamodb_table.table_name,
            },
            service_role_arn=public_api_role.role_arn,
        )

        with open('mapping_templates/get_holiday.json', mode='r') as f:
            get_holiday_json = f.read()

            appsync.CfnResolver(
                self,
                'public_api_resolver_get_holiday',
                api_id=public_api.attr_api_id,
                type_name='Query',
                field_name='getHoliday',
                data_source_name=public_api_datasource.attr_name,
                kind='UNIT',
                request_mapping_template=get_holiday_json,
                response_mapping_template='$util.toJson($context.result)',
            )

        with open('mapping_templates/list_holidays.json', mode='r') as f:
            list_holidays_json = f.read()

            appsync.CfnResolver(
                self,
                'public_api_resolver_list_holidays',
                api_id=public_api.attr_api_id,
                type_name='Query',
                field_name='listHolidays',
                data_source_name=public_api_datasource.attr_name,
                kind='UNIT',
                request_mapping_template=list_holidays_json,
                response_mapping_template='$util.toJson($context.result)',
            )

        # lambda source code upload to s3
        lambda_assets = s3_assets.Asset(
            self,
            'lambda_assets',
            path='./function/.artifact/'
        )

        # update function
        func_api = lambda_.Function(
            self,
            f'{PROJECT}-{STAGE}-func',
            function_name=f'{PROJECT}-{STAGE}-func',
            code=lambda_.Code.from_bucket(
                bucket=lambda_assets.bucket,
                key=lambda_assets.s3_object_key
            ),
            handler='app.handler',
            runtime=lambda_.Runtime.PYTHON_3_7,
            timeout=core.Duration.seconds(120),
            log_retention=logs.RetentionDays.SIX_MONTHS,
            memory_size=128,
            tracing=lambda_.Tracing.ACTIVE,
        )
        func_api.add_environment('TABLE_NAME', dynamodb_table.table_name)
        func_api.add_environment('CSV_URL', CSV_URL)
        func_api.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    'dynamodb:Get*',
                    'dynamodb:Put*',
                    'dynamodb:Batch*',
                ],
                resources=[dynamodb_table.table_arn],
            )
        )

        # schedule execute
        events.Rule(
            self,
            f'{PROJECT}-{STAGE}-schedule',
            enabled=True,
            schedule=events.Schedule.rate(core.Duration.days(10)),
            targets=[events_targets.LambdaFunction(func_api)],
        )

        # lambda@edge
        func_lambdaedge = lambda_.Function(
            self,
            f'{PROJECT}-{STAGE}-func-lambdaedge',
            function_name=f'{PROJECT}-{STAGE}-func-lambdaedge',
            code=lambda_.Code.from_inline(
                open('./function/src/lambdaedge.py').read().replace('__X_API_KEY__', public_api_key.attr_api_key)
            ),
            handler='index.handler',
            runtime=lambda_.Runtime.PYTHON_3_7,
            timeout=core.Duration.seconds(30),
            memory_size=128,
            role=iam.Role(
                self,
                f'{PROJECT}-{STAGE}-func-lambdaedge-role',
                assumed_by=iam.CompositePrincipal(
                    iam.ServicePrincipal('edgelambda.amazonaws.com'),
                    iam.ServicePrincipal('lambda.amazonaws.com'),
                ),
                managed_policies=[
                    iam.ManagedPolicy.from_aws_managed_policy_name('service-role/AWSLambdaBasicExecutionRole'),
                ],
            ),
        )
        lambdaedge_version = func_lambdaedge.add_version(
            hashlib.sha256(
                open('./function/src/lambdaedge.py').read().replace('__X_API_KEY__', public_api_key.attr_api_key).encode()
            ).hexdigest()
        )

        # ACM
        certificates = acm.Certificate(
            self,
            'certificates',
            domain_name=DOMAIN,
            validation_method=acm.ValidationMethod.DNS,
        )

        # CDN
        cdn = cloudfront.CloudFrontWebDistribution(
            self,
            f'{PROJECT}-{STAGE}-cloudfront',
            origin_configs=[cloudfront.SourceConfiguration(
                behaviors=[
                    # default behavior
                    cloudfront.Behavior(
                        allowed_methods=cloudfront.CloudFrontAllowedMethods.ALL,
                        default_ttl=core.Duration.seconds(0),
                        max_ttl=core.Duration.seconds(0),
                        min_ttl=core.Duration.seconds(0),
                        is_default_behavior=True,
                        lambda_function_associations=[
                            cloudfront.LambdaFunctionAssociation(
                                event_type=cloudfront.LambdaEdgeEventType.ORIGIN_REQUEST,
                                lambda_function=lambdaedge_version,
                            ),
                        ]
                    )
                ],
                custom_origin_source=cloudfront.CustomOriginConfig(
                    domain_name=core.Fn.select(2, core.Fn.split('/', public_api.attr_graph_ql_url)),
                ),
            )],
            alias_configuration=cloudfront.AliasConfiguration(
                acm_cert_ref=certificates.certificate_arn,
                names=[DOMAIN],
                security_policy=cloudfront.SecurityPolicyProtocol.TLS_V1_2_2018,
            ),
            price_class=cloudfront.PriceClass.PRICE_CLASS_ALL,
        )
        core.CfnOutput(
            self,
            'cloudfront-domain',
            value=cdn.domain_name,
        )


def main():
    app = core.App()
    MainStack(app, f'{PROJECT}-{STAGE}', env={'region': 'us-east-1'})
    app.synth()


if __name__ == '__main__':
    main()

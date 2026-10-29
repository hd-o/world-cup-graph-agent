import os
from aws_cdk import (
    ArnFormat,
    CfnOutput,
    Duration,
    Environment,
    RemovalPolicy,
    Size,
    Stack,
    Tags,
)
from aws_cdk import aws_bedrockagentcore as agentcore
from aws_cdk import aws_cognito as cognito
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_assets as assets
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct
from scripts.aws.configuration import AWS_TELEMETRY, Targets
from scripts.shared.configuration import ROOT, load_model_environment, load_outputs
from typing import cast


class ApplicationStack(Stack):
    def __init__(self, scope: Construct, targets: Targets) -> None:
        super().__init__(
            scope,
            f"{targets.deployment}-aws",
            env=Environment(account=targets.aws_account, region=targets.aws_region),
        )
        Tags.of(self).add("Deployment", targets.deployment)
        session = targets.create_aws_session()
        zones = session.client("ec2").describe_availability_zones()["AvailabilityZones"]
        supported = {"use1-az1", "use1-az2", "use1-az4"}
        if targets.aws_region != "us-east-1":
            raise ValueError(
                "Verify AgentCore supported AZ IDs before selecting another region"
            )
        availability_zones = sorted(
            zone["ZoneName"] for zone in zones if zone["ZoneId"] in supported
        )[:2]
        if len(availability_zones) != 2:
            raise ValueError("Two supported AgentCore availability zones are required")

        vpc = ec2.Vpc(
            self,
            "Network",
            ip_addresses=ec2.IpAddresses.cidr("10.42.0.0/16"),
            availability_zones=availability_zones,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public", subnet_type=ec2.SubnetType.PUBLIC, cidr_mask=24
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )
        # The VPC construct creates the configured gateway beneath its first
        # public subnet but does not expose the gateway ID at the VPC level.
        nat_gateway = cast(
            ec2.CfnNatGateway,
            vpc.node.find_child("PublicSubnet1").node.find_child("NATGateway"),
        )
        database_group = ec2.SecurityGroup(self, "DatabaseNetwork", vpc=vpc)
        runtime_group = ec2.SecurityGroup(self, "RuntimeNetwork", vpc=vpc)
        database_group.add_ingress_rule(
            runtime_group, ec2.Port.tcp(7687), "Verified private Bolt"
        )
        database_role = iam.Role(
            self,
            "DatabaseIdentity",
            # jsii implementations use different parameter names from their
            # declared interfaces; CDK documents these objects as IPrincipal.
            assumed_by=cast(iam.IPrincipal, iam.ServicePrincipal("ec2.amazonaws.com")),
        )
        database_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "AmazonSSMManagedInstanceCore"
            )
        )
        bootstrap_secret = secretsmanager.CfnSecret(
            self, "DatabaseBootstrap", name=f"{targets.deployment}/database-bootstrap"
        )
        runtime_secret = secretsmanager.CfnSecret(
            self, "DatabaseRuntime", name=f"{targets.deployment}/database-runtime"
        )
        database_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[bootstrap_secret.ref],
            )
        )
        database_role.add_to_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[
                    self.format_arn(
                        service="s3",
                        region="",
                        account="",
                        resource=f"cdk-hnb659fds-assets-{targets.aws_account}-{targets.aws_region}",
                        resource_name=f"{targets.deployment}/bootstrap/*",
                    )
                ],
            )
        )
        database = ec2.Instance(
            self,
            "Database",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                availability_zones=[availability_zones[0]],
            ),
            instance_type=ec2.InstanceType("c7i-flex.large"),
            # Pin compute independently of the persistent disk. Deployment
            # tooling refuses instance changes without a staged replacement plan.
            machine_image=ec2.MachineImage.generic_linux(
                {
                    "us-east-1": "ami-03714e87ed2cafc78",
                }
            ),
            security_group=database_group,
            role=cast(iam.IRole, database_role),
            require_imdsv2=True,
            block_devices=[
                ec2.BlockDevice(
                    device_name="/dev/xvda",
                    volume=ec2.BlockDeviceVolume.ebs(16, encrypted=True),
                ),
            ],
        )
        # Data survives AMI-driven compute replacement. Confirmed teardown must
        # explicitly delete this deployment-owned retained volume after detach.
        recovery = ROOT / "deploy/outputs/aws-recovery.json"
        if recovery.exists():
            record = load_outputs("aws-recovery")
            if (
                record["account"] != targets.aws_account
                or record["region"] != targets.aws_region
                or record["deployment"] != targets.deployment
            ):
                raise PermissionError("Retained database volume target does not match")
            volume = session.client("ec2").describe_volumes(
                VolumeIds=[record["volume_id"]]
            )["Volumes"][0]
            tags = {tag["Key"]: tag["Value"] for tag in volume.get("Tags", [])}
            if (
                tags.get("Deployment") != targets.deployment
                or volume["AvailabilityZone"] != availability_zones[0]
                or not volume["Encrypted"]
            ):
                raise PermissionError(
                    "Retained database volume ownership/location does not match"
                )
            database_volume = ec2.Volume.from_volume_attributes(
                self,
                "DatabaseData",
                volume_id=record["volume_id"],
                availability_zone=availability_zones[0],
            )
        else:
            database_volume = ec2.Volume(
                self,
                "DatabaseData",
                availability_zone=availability_zones[0],
                size=Size.gibibytes(30),
                encrypted=True,
                volume_type=ec2.EbsDeviceVolumeType.GP3,
                removal_policy=RemovalPolicy.RETAIN,
            )
        ec2.CfnVolumeAttachment(
            self,
            "DatabaseDataAttachment",
            instance_id=database.instance_id,
            volume_id=database_volume.volume_id,
            device="/dev/sdf",
        )
        database.user_data.add_commands(
            "set -eu",
            "dnf install -y docker",
            "systemctl enable --now docker",
            "mkdir -p /srv/worldcup",
        )
        zone = route53.PrivateHostedZone(
            self, "PrivateDns", zone_name="worldcup.internal", vpc=vpc
        )
        route53.ARecord(
            self,
            "DatabaseAddress",
            zone=zone,
            record_name="neo4j",
            target=route53.RecordTarget.from_ip_addresses(database.instance_private_ip),
        )

        pool = cognito.UserPool(
            self,
            "Users",
            user_pool_name=f"{targets.deployment}-users",
            self_sign_up_enabled=False,
            sign_in_aliases=cognito.SignInAliases(username=True),
            removal_policy=RemovalPolicy.DESTROY,
            password_policy=cognito.PasswordPolicy(min_length=12),
        )
        client = pool.add_client(
            "PublicClient",
            generate_secret=False,
            auth_flows=cognito.AuthFlow(user_password=True),
            prevent_user_existence_errors=True,
        )
        runtime_role = iam.Role(
            self,
            "RuntimeIdentity",
            assumed_by=cast(
                iam.IPrincipal,
                iam.ServicePrincipal(
                    "bedrock-agentcore.amazonaws.com",
                    conditions={
                        "StringEquals": {"aws:SourceAccount": targets.aws_account},
                        "ArnLike": {
                            "aws:SourceArn": self.format_arn(
                                service="bedrock-agentcore",
                                resource="runtime",
                                resource_name=targets.deployment.replace("-", "_")
                                + "-*",
                            )
                        },
                    },
                ),
            ),
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[
                    runtime_secret.ref,
                    self.format_arn(
                        service="secretsmanager",
                        resource="secret",
                        resource_name=f"{targets.deployment}/users/*",
                        arn_format=ArnFormat.COLON_RESOURCE_NAME,
                    ),
                ],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "xray:PutTraceSegments",
                    "xray:PutTelemetryRecords",
                    "xray:GetSamplingRules",
                    "xray:GetSamplingTargets",
                ],
                resources=["*"],
            )
        )
        application_logs = logs.LogGroup(
            self,
            "ApplicationLogs",
            log_group_name=f"/aws/bedrock-agentcore/{targets.deployment}/application",
            retention=logs.RetentionDays.ONE_MONTH,
            removal_policy=RemovalPolicy.DESTROY,
        )
        application_logs.add_to_resource_policy(
            iam.PolicyStatement(
                principals=[
                    cast(iam.IPrincipal, iam.ServicePrincipal("xray.amazonaws.com"))
                ],
                actions=["logs:CreateLogStream", "logs:PutLogEvents"],
                resources=[application_logs.log_group_arn + ":*"],
                conditions={"StringEquals": {"aws:SourceAccount": targets.aws_account}},
            )
        )
        # Runtime's public CDK construct supplies broad defaults. Explicit denies
        # constrain those defaults without relying on private construct internals.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["bedrock-agentcore:GetWorkloadAccessToken*"],
                resources=["*"],
            )
        )
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                    "logs:PutResourcePolicy",
                    "logs:DescribeLogStreams",
                ],
                not_resources=[
                    application_logs.log_group_arn,
                    application_logs.log_group_arn + ":*",
                    self.format_arn(
                        service="logs",
                        resource="log-group",
                        arn_format=ArnFormat.COLON_RESOURCE_NAME,
                        resource_name="/aws/bedrock-agentcore/runtimes/"
                        + targets.deployment.replace("-", "_")
                        + "-*",
                    ),
                ],
            )
        )
        # AgentCore configures its runtime-scoped delivery policy during startup.
        # Limit this permission to this deployment's log groups.
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                actions=["logs:PutResourcePolicy"],
                resources=[
                    application_logs.log_group_arn,
                    self.format_arn(
                        service="logs",
                        resource="log-group",
                        arn_format=ArnFormat.COLON_RESOURCE_NAME,
                        resource_name="/aws/bedrock-agentcore/runtimes/"
                        + targets.deployment.replace("-", "_")
                        + "-*",
                    ),
                ],
            )
        )
        artifact = assets.Asset(
            self, "AgentArtifact", path=os.environ["DEPLOY_AWS_ARTIFACT"]
        )
        artifact.grant_read(runtime_role)
        runtime_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.DENY,
                actions=["s3:GetObject*"],
                not_resources=[
                    artifact.bucket.bucket_arn + "/" + artifact.s3_object_key
                ],
            )
        )
        runtime = agentcore.Runtime(
            self,
            "Agent",
            runtime_name=targets.deployment.replace("-", "_"),
            agent_runtime_artifact=agentcore.AgentRuntimeArtifact.from_s3(
                s3.Location(
                    bucket_name=artifact.s3_bucket_name,
                    object_key=artifact.s3_object_key,
                ),
                runtime=agentcore.AgentCoreRuntime.PYTHON_3_14,
                entrypoint=[
                    "opentelemetry-instrument",
                    "serve.py",
                ],
            ),
            execution_role=cast(iam.IRole, runtime_role),
            # Service usage logs contain consumption metadata. Invocation
            # application logs contain raw request/response payloads, outside
            # the application's filtered OTEL export boundary.
            logging_configs=[
                agentcore.LoggingConfig(
                    destination=agentcore.LoggingDestination.cloud_watch_logs(
                        application_logs
                    ),
                    log_type=agentcore.LogType.USAGE_LOGS,
                )
            ],
            authorizer_configuration=agentcore.RuntimeAuthorizerConfiguration.using_cognito(
                pool, [client]
            ),
            request_header_configuration=agentcore.RequestHeaderConfiguration(
                allowlisted_headers=["Authorization"]
            ),
            network_configuration=agentcore.RuntimeNetworkConfiguration.using_vpc(
                self,
                vpc=vpc,
                security_groups=[runtime_group],
                vpc_subnets=ec2.SubnetSelection(
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
                ),
            ),
            lifecycle_configuration=agentcore.LifecycleConfiguration(
                idle_runtime_session_timeout=Duration.minutes(5),
                max_lifetime=Duration.minutes(30),
            ),
            environment_variables=AWS_TELEMETRY
            | load_model_environment()
            | {
                "ENVIRONMENT": "demo",
                "RUNTIME": "aws",
                "DEPLOYMENT_NAME": targets.deployment,
                "NEO4J_URI": "bolt://neo4j.worldcup.internal:7687",
                "COGNITO_ISSUER": f"https://cognito-idp.{targets.aws_region}.amazonaws.com/{pool.user_pool_id}",
                "COGNITO_CLIENT_ID": client.user_pool_client_id,
                "OTEL_SERVICE_NAME": f"{targets.deployment}-agent",
                "AWS_REGION": targets.aws_region,
                "APPLICATION_LOG_GROUP": application_logs.log_group_name,
            },
            tracing_enabled=True,
        )
        for name, value in {
            "RuntimeArn": runtime.agent_runtime_arn,
            "RuntimeId": runtime.agent_runtime_id,
            "UserPoolId": pool.user_pool_id,
            "ClientId": client.user_pool_client_id,
            "DatabaseInstance": database.instance_id,
            "DatabaseVolume": database_volume.volume_id,
            "DatabaseBootstrapSecret": bootstrap_secret.ref,
            "DatabaseRuntimeSecret": runtime_secret.ref,
            "VpcId": vpc.vpc_id,
            "InternetGatewayId": cast(str, vpc.internet_gateway_id),
            "NatGatewayId": nat_gateway.ref,
            "RuntimeSecurityGroupId": runtime_group.security_group_id,
            "DatabaseSecurityGroupId": database_group.security_group_id,
        }.items():
            CfnOutput(self, name, value=value)

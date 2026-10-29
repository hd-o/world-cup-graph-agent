import json
import time
from botocore.exceptions import WaiterError
from hashlib import sha256
from scripts.aws.configuration import Targets
from scripts.aws.deployment import find_stack, list_stack_resources, read_tags
from scripts.shared.configuration import save_outputs
from scripts.shared.interface import ask_text, show_inventory
from shlex import join
from typing import Any


DATA_LOSS_CONFIRMATION = "DELETE_DATABASE_USERS_AND_KEYS"
NETWORK_RESOURCES = (
    ("instance", "describe_instances", "Reservations", "InstanceId"),
    ("nat_gateway", "describe_nat_gateways", "NatGateways", "NatGatewayId"),
    ("endpoint", "describe_vpc_endpoints", "VpcEndpoints", "VpcEndpointId"),
    (
        "interface",
        "describe_network_interfaces",
        "NetworkInterfaces",
        "NetworkInterfaceId",
    ),
    ("route_table", "describe_route_tables", "RouteTables", "RouteTableId"),
    ("security_group", "describe_security_groups", "SecurityGroups", "GroupId"),
    (
        "internet_gateway",
        "describe_internet_gateways",
        "InternetGateways",
        "InternetGatewayId",
    ),
    ("address", "describe_addresses", "Addresses", "AllocationId"),
    ("subnet", "describe_subnets", "Subnets", "SubnetId"),
    ("vpc", "describe_vpcs", "Vpcs", "VpcId"),
    ("volume", "describe_volumes", "Volumes", "VolumeId"),
)


class CleanupPending(Exception):
    pass


def has_agentcore_management(interface: dict) -> bool:
    # RequesterManaged can be false for AgentCore's Amazon-owned attachments.
    # https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.html
    return (
        interface.get("InterfaceType") == "agentic_ai"
        and read_tags(interface).get("AmazonBedrockAgentCoreManaged") == "true"
        and interface.get("Attachment", {}).get("InstanceOwnerId") == "amazon-aws"
    )


def build_agentcore_cleanup_guidance(targets: Targets, interfaces: list[dict]) -> dict:
    routing = [
        "poe",
        "aws",
        "--profile",
        targets.aws_profile,
        "--account",
        targets.aws_account,
        "--region",
        targets.aws_region,
    ]
    return {
        "interfaces": interfaces,
        "notice": (
            "AgentCore VPC interfaces can persist for up to eight hours after "
            "runtime deletion, blocking security-group, subnet and VPC cleanup."
        ),
        "next_action": (
            "Wait for AWS to release the interfaces, preview the remaining scope, "
            "then retry destroy with fresh confirmations. Do not force-detach. "
            "Contact AWS Support if interfaces persist beyond eight hours."
        ),
        "preview_command": join([*routing, "destroy", "--preview"]),
        "retry_command": join(
            [
                *routing,
                "destroy",
                "--execute",
                "--confirm-target",
                targets.aws_account,
                "--confirm-deployment",
                targets.deployment,
                "--confirm-data-loss",
                DATA_LOSS_CONFIRMATION,
            ]
        ),
        "documentation": (
            "https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/"
            "agentcore-vpc.html"
        ),
    }


def report_agentcore_cleanup_pending(
    targets: Targets,
    session: Any,
    inventory: dict,
) -> bool:
    current = discover_inventory(targets, session)
    interfaces = current["agentcore_interfaces"]
    if not interfaces or current["stack_status"] not in (
        "DELETE_IN_PROGRESS",
        "DELETE_FAILED",
        "DELETE_COMPLETE",
        "ABSENT",
    ):
        return False

    runtime_resources = [
        resource
        for resource in inventory["stack_resources"]
        if resource["type"] == "AWS::BedrockAgentCore::Runtime"
    ] + [
        resource
        for resource in inventory["application_orphans"]
        if resource["type"] == "bedrock-agentcore"
        and resource["metadata"].get("subtype") == "runtime"
    ]
    if not runtime_resources:
        return False

    runtime_identities = set()
    for resource in runtime_resources:
        identity = resource["identity"]
        if not identity:
            return False
        if not identity.startswith("arn:"):
            identity = (
                f"arn:aws:bedrock-agentcore:{targets.aws_region}:"
                f"{targets.aws_account}:runtime/{identity}"
            )
        verify_resource_destination(identity, targets)
        runtime_identities.add(identity)
    for identity in runtime_identities:
        if describe_application_resource(session, "bedrock-agentcore", identity):
            return False

    dependencies = {
        identity
        for interface in interfaces
        for identity in (interface["vpc"], interface["subnet"], *interface["groups"])
    }
    failures = [
        resource
        for resource in current["stack_resources"]
        if resource["status"] == "DELETE_FAILED"
    ]
    # Do not reclassify unrelated CloudFormation failures as a service delay.
    if any(
        resource["type"]
        not in (
            "AWS::EC2::Subnet",
            "AWS::EC2::SecurityGroup",
            "AWS::EC2::VPC",
        )
        or resource["identity"] not in dependencies
        for resource in failures
    ) or (current["stack_status"] == "DELETE_FAILED" and not failures):
        return False

    formation = session.client("cloudformation")
    if any(
        not has_network_dependency_failure(formation, current["stack"], resource)
        for resource in failures
    ):
        return False

    guidance = build_agentcore_cleanup_guidance(targets, interfaces)
    report = current | {
        "status": "cleanup pending; awaiting AWS AgentCore interface release",
        "agentcore_cleanup": guidance,
        "retained_storage": "Held until stack deletion is confirmed",
        "exit_code": 2,
    }
    save_inventory(report)
    show_inventory(
        "AWS cleanup pending — not complete (exit code 2)",
        {
            "stack": current["stack"],
            "stack_status": current["stack_status"],
            "status": report["status"],
            "runtime": "Deletion confirmed",
            "retained_storage": report["retained_storage"],
            **guidance,
        },
    )
    return True


def has_network_dependency_failure(
    formation: Any,
    stack: str,
    resource: dict,
) -> bool:
    details = formation.describe_stack_resource(
        StackName=stack,
        LogicalResourceId=resource["logical_identity"],
    )["StackResourceDetail"]
    if (
        details.get("PhysicalResourceId") != resource["identity"]
        or details.get("ResourceStatus") != "DELETE_FAILED"
    ):
        return False

    # A dependency-shaped resource ID is not proof of a dependency failure.
    # Inspect native reasons in memory without exposing arbitrary SDK text.
    reason = details.get("ResourceStatusReason", "").casefold()
    if any(
        token in reason
        for token in (
            "accessdenied",
            "access denied",
            "unauthorized",
            "not authorized",
            "permission",
            "forbidden",
        )
    ):
        return False
    return resource["identity"].casefold() in reason and any(
        token in reason
        for token in (
            "dependencyviolation",
            "has dependencies",
            "has a dependent object",
        )
    )


def save_inventory(inventory: dict) -> None:
    # Native inventories include datetime objects. Reports are JSON conveniences,
    # while deletion comparisons retain the original in-memory native metadata.
    save_outputs("aws-destroy", json.loads(json.dumps(inventory, default=str)))


def add_owned_candidate(
    candidates: list[dict],
    identities: set[str],
    identity: str,
    tags: dict[str, str],
    targets: Targets,
) -> None:
    if identity in identities or tags.get("Deployment") != targets.deployment:
        return
    verify_resource_destination(identity, targets)
    candidates.append(
        {
            "ResourceARN": identity,
            "Tags": [{"Key": name, "Value": value} for name, value in tags.items()],
        }
    )
    identities.add(identity)


def list_application_candidates(session: Any, targets: Targets) -> list[dict]:
    candidates = [
        resource
        for page in session.client("resourcegroupstaggingapi")
        .get_paginator("get_resources")
        .paginate(TagFilters=[{"Key": "Deployment", "Values": [targets.deployment]}])
        for resource in page["ResourceTagMappingList"]
    ]
    identities = {candidate["ResourceARN"] for candidate in candidates}
    # Native enumeration covers services whose resources are absent from the
    # tagging index; it also makes pending asynchronous runtime deletion visible.
    runtime = session.client("bedrock-agentcore-control")
    token = None
    while True:
        page = runtime.list_agent_runtimes(**({"nextToken": token} if token else {}))
        for resource in page.get("agentRuntimes", []):
            identity = resource["agentRuntimeArn"]
            tags = runtime.list_tags_for_resource(resourceArn=identity)["tags"]
            if tags.get("Deployment") == targets.deployment:
                add_owned_candidate(candidates, identities, identity, tags, targets)
                for endpoints in runtime.get_paginator(
                    "list_agent_runtime_endpoints"
                ).paginate(agentRuntimeId=resource["agentRuntimeId"]):
                    for endpoint in endpoints["runtimeEndpoints"]:
                        identity = endpoint["agentRuntimeEndpointArn"]
                        tags = runtime.list_tags_for_resource(resourceArn=identity)[
                            "tags"
                        ]
                        add_owned_candidate(
                            candidates, identities, identity, tags, targets
                        )
        token = page.get("nextToken")
        if not token:
            break
    directory = session.client("cognito-idp")
    token = None
    while True:
        page = directory.list_user_pools(
            MaxResults=60, **({"NextToken": token} if token else {})
        )
        for resource in page.get("UserPools", []):
            identity = (
                f"arn:aws:cognito-idp:{targets.aws_region}:{targets.aws_account}:"
                f"userpool/{resource['Id']}"
            )
            if identity in identities:
                continue
            tags = directory.list_tags_for_resource(ResourceArn=identity)["Tags"]
            if tags.get("Deployment") == targets.deployment:
                candidates.append(
                    {
                        "ResourceARN": identity,
                        "Tags": [
                            {"Key": name, "Value": value}
                            for name, value in tags.items()
                        ],
                    }
                )
                identities.add(identity)
        token = page.get("NextToken")
        if not token:
            break
    for page in runtime.get_paginator("list_workload_identities").paginate():
        for workload in page["workloadIdentities"]:
            identity = workload["workloadIdentityArn"]
            tags = runtime.list_tags_for_resource(resourceArn=identity)["tags"]
            add_owned_candidate(candidates, identities, identity, tags, targets)
    accounts = session.client("iam")
    for page in accounts.get_paginator("list_roles").paginate():
        for role in page["Roles"]:
            metadata = accounts.get_role(RoleName=role["RoleName"])["Role"]
            add_owned_candidate(
                candidates, identities, metadata["Arn"], read_tags(metadata), targets
            )
    for page in accounts.get_paginator("list_instance_profiles").paginate():
        for profile in page["InstanceProfiles"]:
            metadata = accounts.get_instance_profile(
                InstanceProfileName=profile["InstanceProfileName"]
            )["InstanceProfile"]
            add_owned_candidate(
                candidates, identities, metadata["Arn"], read_tags(metadata), targets
            )
    logs = session.client("logs")
    for operation, field in (
        ("describe_deliveries", "deliveries"),
        ("describe_delivery_sources", "deliverySources"),
        ("describe_delivery_destinations", "deliveryDestinations"),
    ):
        for page in logs.get_paginator(operation).paginate():
            for delivery in page[field]:
                add_owned_candidate(
                    candidates,
                    identities,
                    delivery["arn"],
                    delivery.get("tags", {}),
                    targets,
                )
    return candidates


def describe_log_resource(client: Any, identity: str) -> dict | None:
    kind, _, name = identity.split(":", 5)[5].partition(":")
    if kind == "log-group":
        name = name.removesuffix(":*")
        groups = client.describe_log_groups(logGroupNamePrefix=name)["logGroups"]
        group = next((group for group in groups if group["logGroupName"] == name), None)
        if group is None:
            return None
        tags = client.list_tags_log_group(logGroupName=name)["tags"]
        return group | {
            "subtype": "log_group",
            "Tags": [{"Key": name, "Value": value} for name, value in tags.items()],
        }
    match kind:
        case "delivery-source":
            metadata = client.get_delivery_source(name=name)["deliverySource"]
        case "delivery-destination":
            metadata = client.get_delivery_destination(name=name)["deliveryDestination"]
        case "delivery":
            metadata = client.get_delivery(id=name)["delivery"]
        case _:
            return {"unsupported": True}
    if metadata["arn"] != identity:
        raise PermissionError("Native log-delivery ARN differs from discovery")
    tags = client.list_tags_for_resource(resourceArn=identity)["tags"]
    return metadata | {
        "subtype": kind,
        "Tags": [{"Key": name, "Value": value} for name, value in tags.items()],
    }


def describe_agentcore_resource(client: Any, identity: str) -> dict:
    parts = identity.split(":", 5)[5].split("/")
    if len(parts) == 4 and parts[0] == "runtime" and parts[2] == "runtime-endpoint":
        metadata = client.get_agent_runtime_endpoint(
            agentRuntimeId=parts[1], endpointName=parts[3]
        )
        if metadata["agentRuntimeEndpointArn"] != identity:
            raise PermissionError("Native runtime endpoint ARN differs from discovery")
        metadata = {
            "subtype": "runtime_endpoint",
            "runtime_id": parts[1],
            "name": metadata["name"],
            "status": metadata["status"],
            "service_managed": metadata["name"] == "DEFAULT",
        }
    elif (
        len(parts) == 4
        and parts[0] == "workload-identity-directory"
        and parts[2] == "workload-identity"
    ):
        metadata = client.get_workload_identity(name=parts[3])
        if metadata["workloadIdentityArn"] != identity:
            raise PermissionError("Native workload identity ARN differs from discovery")
        metadata = {"subtype": "workload_identity", "name": metadata["name"]}
    elif len(parts) == 2 and parts[0] == "runtime":
        metadata = client.get_agent_runtime(agentRuntimeId=parts[1])
        if metadata["agentRuntimeArn"] != identity:
            raise PermissionError("Native runtime ARN differs from discovery")
        # Runtime environment values and arbitrary failure text are not inventory
        # metadata. Keep credentials out of even protected convenience reports.
        metadata = {
            "subtype": "runtime",
            "runtime_id": parts[1],
            "status": metadata["status"],
            "workload_identity": metadata.get("workloadIdentityDetails", {}).get(
                "workloadIdentityArn"
            ),
        }
    else:
        return {"unsupported": True}
    tags = client.list_tags_for_resource(resourceArn=identity)["tags"]
    return metadata | {
        "Tags": [{"Key": name, "Value": value} for name, value in tags.items()],
    }


def capture_stack_revision(formation: Any, stack: dict) -> dict:
    template = formation.get_template(StackName=stack["StackId"])["TemplateBody"]
    resources = list_stack_resources(formation, stack["StackId"])
    return {
        "identity": stack["StackId"],
        "status": stack["StackStatus"],
        "creation_time": stack["CreationTime"],
        "update_time": stack.get("LastUpdatedTime"),
        "change_set": stack.get("ChangeSetId"),
        "template_sha256": sha256(
            json.dumps(template, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "resources": sorted(
            resources, key=lambda resource: resource["logical_identity"]
        ),
    }


def describe_instance_disks(
    compute: Any, instance: dict, targets: Targets
) -> list[dict]:
    mappings = instance.get("BlockDeviceMappings", [])
    disks: list[dict] = []
    image = None
    for mapping in mappings:
        if "Ebs" not in mapping:
            continue
        identity = mapping["Ebs"]["VolumeId"]
        volume = compute.describe_volumes(VolumeIds=[identity])["Volumes"][0]
        attachments = volume.get("Attachments", [])
        attachment = next(
            (
                attachment
                for attachment in attachments
                if attachment["InstanceId"] == instance["InstanceId"]
                and attachment["Device"] == mapping["DeviceName"]
            ),
            None,
        )
        ownership = None
        if attachment is not None and len(attachments) == 1:
            lineage = read_resource_lineage(volume, targets)
            if lineage:
                ownership = f"deployment volume with immutable stack lineage {lineage}"
            elif not volume.get("Tags") and mapping["DeviceName"] == instance.get(
                "RootDeviceName"
            ):
                # A root-device name alone can hide a manually replaced data disk.
                # The AMI snapshot and launch-time attachment prove boot origin.
                if image is None:
                    images = compute.describe_images(ImageIds=[instance["ImageId"]])[
                        "Images"
                    ]
                    image = images[0] if images else {}
                snapshot = next(
                    (
                        device["Ebs"].get("SnapshotId")
                        for device in image.get("BlockDeviceMappings", [])
                        if device["DeviceName"] == image.get("RootDeviceName")
                        and "Ebs" in device
                    ),
                    None,
                )
                creation_delay = abs(
                    (volume["CreateTime"] - instance["LaunchTime"]).total_seconds()
                )
                attachment_delay = abs(
                    (attachment["AttachTime"] - instance["LaunchTime"]).total_seconds()
                )
                if (
                    snapshot
                    and volume.get("SnapshotId") == snapshot
                    and creation_delay <= 120
                    and attachment_delay <= 120
                ):
                    ownership = "original boot disk: native image and launch timestamps"
        disks.append(
            {
                "identity": identity,
                "device": mapping["DeviceName"],
                "action": "delete on instance termination"
                if mapping["Ebs"]["DeleteOnTermination"]
                else "detach and retain",
                "ownership": ownership,
            }
        )
    return sorted(disks, key=lambda disk: disk["device"])


def verify_instance_disks(
    compute: Any,
    instance: dict,
    snapshot: dict,
    targets: Targets,
) -> None:
    deployment = read_tags(instance).get("Deployment")
    if deployment is not None and deployment != targets.deployment:
        raise PermissionError("Instance carries conflicting deployment ownership")
    mappings = sorted(
        instance.get("BlockDeviceMappings", []),
        key=lambda mapping: mapping["DeviceName"],
    )
    reviewed_mappings = sorted(
        snapshot.get("BlockDeviceMappings", []),
        key=lambda mapping: mapping["DeviceName"],
    )
    if mappings != reviewed_mappings:
        raise PermissionError("Instance disk mappings changed; rediscover/reconfirm")
    disks = describe_instance_disks(compute, instance, targets)
    if disks != snapshot["implicit_disk_changes"] or any(
        not disk["ownership"] for disk in disks
    ):
        raise PermissionError("Instance disk scope is ambiguous or changed")
    instances = list_native_resources(
        compute,
        "describe_instances",
        "Reservations",
        [{"Name": "instance-id", "Values": [instance["InstanceId"]]}],
    )
    if len(instances) != 1:
        raise PermissionError("Reviewed instance disappeared; rediscover")
    mappings = sorted(
        instances[0].get("BlockDeviceMappings", []),
        key=lambda mapping: mapping["DeviceName"],
    )
    if (
        mappings != reviewed_mappings
        or read_tags(instances[0]).get("Deployment") != deployment
    ):
        raise PermissionError("Instance changed immediately before deletion")


def wait_for_agentcore_deletion(session: Any, identity: str) -> None:
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        metadata = describe_application_resource(session, "bedrock-agentcore", identity)
        if metadata is None:
            return
        if metadata.get("status") == "DELETE_FAILED":
            raise RuntimeError("AgentCore deletion failed; inspect the owned resource")
        time.sleep(5)
    raise TimeoutError("AgentCore deletion pending; dependent networking preserved")


def wait_for_service_interfaces(compute: Any, vpcs: set[str]) -> None:
    deadline = time.monotonic() + 120
    while True:
        interfaces = [
            interface
            for vpc in vpcs
            for interface in list_native_resources(
                compute,
                "describe_network_interfaces",
                "NetworkInterfaces",
                [{"Name": "vpc-id", "Values": [vpc]}],
            )
            if interface.get("RequesterManaged") or has_agentcore_management(interface)
        ]
        if not interfaces:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Service interfaces remain; networking preserved")
        time.sleep(3)


def read_group_references(metadata: dict) -> set[str]:
    return {
        pair["GroupId"]
        for field in ("IpPermissions", "IpPermissionsEgress")
        for permission in metadata.get(field, [])
        for pair in permission.get("UserIdGroupPairs", [])
        if pair.get("GroupId") and pair["GroupId"] != metadata["GroupId"]
    }


def order_network_resources(resources: list[dict]) -> list[dict]:
    groups = {
        resource["identity"]: resource
        for resource in resources
        if resource["type"] == "security_group"
    }
    ranks = {}
    while groups:
        references = set().union(
            *(
                read_group_references(resource["metadata"])
                for resource in groups.values()
            )
        )
        identities = sorted(set(groups) - references)
        if not identities:
            break
        for identity in identities:
            ranks[identity] = len(ranks)
            del groups[identity]
    priorities = {
        resource[0]: position for position, resource in enumerate(NETWORK_RESOURCES)
    }
    return sorted(
        [
            resource | {"dependency_cycle": resource["identity"] in groups}
            for resource in resources
        ],
        key=lambda resource: (
            priorities[resource["type"]],
            ranks.get(resource["identity"], len(ranks)),
            resource["identity"],
        ),
    )


def read_application_priority(resource: dict) -> tuple[int, str]:
    priorities = {
        "runtime_endpoint": 0,
        "runtime": 1,
        "workload_identity": 2,
        "delivery": 3,
        "delivery-source": 4,
        "delivery-destination": 5,
        "log_group": 6,
    }
    metadata = resource["metadata"]
    priority = priorities.get(metadata.get("subtype", ""), 7)
    # The owned role removes its verified profile association; a profile must
    # follow that operation rather than failing while its role is attached.
    if resource["type"] == "iam" and "InstanceProfileName" in metadata:
        priority = 8
    if metadata.get("service_managed"):
        priority = 2
    return priority, resource["identity"]


def list_reviewed_remainders(session: Any, inventory: dict) -> list[dict]:
    remainders = []
    for resource in inventory["application_orphans"]:
        metadata = describe_application_resource(
            session, resource["type"], resource["identity"]
        )
        if metadata is not None:
            remainders.append(
                {
                    "type": resource["type"],
                    "identity": resource["identity"],
                    "subtype": metadata.get("subtype"),
                    "reason": (
                        "reviewed resource still exists; ownership must be rechecked"
                    ),
                }
            )
    filters = {
        "instance": "instance-id",
        "nat_gateway": "nat-gateway-id",
        "endpoint": "vpc-endpoint-id",
        "interface": "network-interface-id",
        "route_table": "route-table-id",
        "security_group": "group-id",
        "internet_gateway": "internet-gateway-id",
        "address": "allocation-id",
        "subnet": "subnet-id",
        "vpc": "vpc-id",
        "volume": "volume-id",
    }
    compute = session.client("ec2")
    volume_ids = {
        resource["identity"]
        for resource in inventory["resources"]
        if resource["type"] == "volume"
    }
    for resource in inventory["resources"]:
        operation, field, identity_field = next(
            (operation, field, identity_field)
            for kind, operation, field, identity_field in NETWORK_RESOURCES
            if kind == resource["type"]
        )
        metadata = list_native_resources(
            compute,
            operation,
            field,
            [{"Name": filters[resource["type"]], "Values": [resource["identity"]]}],
        )
        for remaining in metadata:
            if (
                resource["type"] == "instance"
                and remaining["State"]["Name"] == "terminated"
            ):
                continue
            if resource["type"] == "nat_gateway" and remaining["State"] == "deleted":
                continue
            remainders.append(
                {
                    "type": resource["type"],
                    "identity": remaining[identity_field],
                    "reason": "reviewed resource still exists; rediscover owned scope",
                }
            )
        for disk in resource["metadata"].get("implicit_disk_changes", []):
            if disk["identity"] in volume_ids:
                continue
            disks = list_native_resources(
                compute,
                "describe_volumes",
                "Volumes",
                [{"Name": "volume-id", "Values": [disk["identity"]]}],
            )
            if disks:
                remainders.append(
                    {
                        "type": "volume",
                        "identity": disk["identity"],
                        "reason": "implicit disk remains; no expanded deletion scope",
                        "reviewed_action": disk["action"],
                    }
                )
    return remainders


def verify_resource_destination(identity: str, targets: Targets) -> None:
    parts = identity.split(":", 5)
    if (
        len(parts) != 6
        or parts[0] != "arn"
        or parts[4] not in ({""} if parts[2] == "route53" else {targets.aws_account})
        or parts[3] not in ("", targets.aws_region)
    ):
        raise PermissionError("Resource account/region does not match target")


def read_resource_lineage(resource: dict, targets: Targets) -> str | None:
    tags = read_tags(resource)
    prefix = (
        f"arn:aws:cloudformation:{targets.aws_region}:{targets.aws_account}:"
        f"stack/{targets.deployment}-aws/"
    )
    lineage = tags.get("aws:cloudformation:stack-id", "")
    if tags.get("Deployment") == targets.deployment and lineage.startswith(prefix):
        return lineage
    return None


def describe_lineage(formation: Any, identity: str, targets: Targets) -> str:
    # System CloudFormation tags are immutable ownership evidence even when the
    # deleted stack record has aged out of CloudFormation's retained history.
    stack = find_stack(targets, formation, identity)
    return stack["StackStatus"] if stack is not None else "ABSENT"


def list_native_resources(
    compute: Any, operation: str, field: str, filters: list[dict]
) -> list[dict]:
    pages = (
        compute.get_paginator(operation).paginate(Filters=filters)
        if compute.can_paginate(operation)
        else [getattr(compute, operation)(Filters=filters)]
    )
    resources = [resource for page in pages for resource in page[field]]
    if operation == "describe_instances":
        return [
            instance
            for reservation in resources
            for instance in reservation["Instances"]
        ]
    if operation == "describe_network_interfaces":
        return [
            resource | {"Tags": resource.get("TagSet", [])} for resource in resources
        ]
    return resources


def discover_inventory(targets: Targets, session: Any) -> dict:
    formation = session.client("cloudformation")
    stack = find_stack(targets, formation)
    current = (
        stack["StackId"]
        if stack and stack["StackStatus"] != "DELETE_COMPLETE"
        else None
    )
    lineage_status: dict[str, str] = {}
    if stack:
        lineage_status[stack["StackId"]] = stack["StackStatus"]
    stack_revision = capture_stack_revision(formation, stack) if stack else None
    stack_resources = stack_revision["resources"] if stack_revision else []
    stack_instances = {
        resource["identity"]
        for resource in stack_resources
        if resource["type"] == "AWS::EC2::Instance" and resource["identity"]
    }
    compute = session.client("ec2")
    resources: list[dict] = []
    ambiguous: list[dict] = []
    for kind, operation, field, identity_field in NETWORK_RESOURCES:
        candidates = list_native_resources(
            compute,
            operation,
            field,
            [{"Name": "tag:Deployment", "Values": [targets.deployment]}],
        )
        if kind == "instance":
            identities = {resource["InstanceId"] for resource in candidates}
            for identity in stack_instances - identities:
                candidates.extend(
                    list_native_resources(
                        compute,
                        operation,
                        field,
                        [{"Name": "instance-id", "Values": [identity]}],
                    )
                )
        for resource in candidates:
            if kind == "instance" and resource["State"]["Name"] == "terminated":
                continue
            if kind == "nat_gateway" and resource["State"] == "deleted":
                continue
            lineage = read_resource_lineage(resource, targets)
            if (
                kind == "instance"
                and resource["InstanceId"] in stack_instances
                and current
            ):
                lineage = current
            if not lineage:
                ambiguous.append(
                    {
                        "type": kind,
                        "identity": resource[identity_field],
                        "reason": "deployment tag without CloudFormation lineage",
                    }
                )
                continue
            if kind == "instance":
                disks = describe_instance_disks(compute, resource, targets)
                resource = resource | {"implicit_disk_changes": disks}
                ambiguous.extend(
                    {
                        "type": "volume",
                        "identity": disk["identity"],
                        "reason": "unverified implicit instance disk change",
                        "instance": resource["InstanceId"],
                        "action": disk["action"],
                    }
                    for disk in disks
                    if not disk["ownership"]
                )
            if lineage not in lineage_status:
                lineage_status[lineage] = describe_lineage(formation, lineage, targets)
            resources.append(
                {
                    "type": kind,
                    "identity": resource[identity_field],
                    "lineage": lineage,
                    "scope": "stack" if lineage == current else "orphan",
                    "metadata": resource,
                }
            )
    # Service-controlled ENIs can be untagged. Association with verified owned
    # VPC/subnet/groups identifies pending dependencies, not permission to detach.
    vpcs = {resource["identity"] for resource in resources if resource["type"] == "vpc"}
    known = {resource["identity"] for resource in resources}
    agentcore_interfaces = []
    for vpc in vpcs:
        for interface in list_native_resources(
            compute,
            "describe_network_interfaces",
            "NetworkInterfaces",
            [{"Name": "vpc-id", "Values": [vpc]}],
        ):
            if interface.get(
                "OwnerId"
            ) == targets.aws_account and has_agentcore_management(interface):
                agentcore_interfaces.append(
                    {
                        "identity": interface["NetworkInterfaceId"],
                        "vpc": interface["VpcId"],
                        "subnet": interface["SubnetId"],
                        "groups": [group["GroupId"] for group in interface["Groups"]],
                        "status": interface["Status"],
                    }
                )
            if interface["NetworkInterfaceId"] not in known:
                ambiguous.append(
                    {
                        "type": "interface",
                        "identity": interface["NetworkInterfaceId"],
                        "reason": (
                            "untagged VPC dependency; never force-detach interfaces"
                        ),
                    }
                )
    store = session.client("secretsmanager")
    secrets = []
    for page in store.get_paginator("list_secrets").paginate(
        IncludePlannedDeletion=True
    ):
        for secret in page["SecretList"]:
            if read_tags(secret).get("Deployment") != targets.deployment:
                continue
            verify_resource_destination(secret["ARN"], targets)
            metadata = store.describe_secret(SecretId=secret["ARN"])
            if read_tags(metadata).get("Deployment") != targets.deployment:
                raise PermissionError("Secret ownership changed during discovery")
            if metadata["Name"].startswith(f"{targets.deployment}/users/") and metadata[
                "Name"
            ].endswith("/openrouter"):
                secrets.append(metadata["ARN"])
    # Tagging inventories are discovery hints. Native service reads below prove
    # that a candidate exists and still carries the reviewed ownership evidence.
    applications: list[dict] = []
    candidates = list_application_candidates(session, targets)
    managed_identities = {resource["identity"] for resource in stack_resources}
    for candidate in candidates:
        identity = candidate["ResourceARN"]
        service = identity.split(":")[2]
        if service == "ec2" or identity in secrets or identity == current:
            continue
        lineage = read_resource_lineage(candidate, targets)
        if not lineage:
            metadata = describe_application_resource(session, service, identity)
            if metadata is None:
                continue
            identities = {
                identity,
                identity.rsplit("/", 1)[-1],
                identity.split(":log-group:")[-1].removesuffix(":*"),
            }
            if not identities & managed_identities:
                ambiguous.append(
                    {
                        "type": service,
                        "identity": identity,
                        "reason": "deployment tagging hint without verified lineage",
                    }
                )
            continue
        if lineage not in lineage_status:
            lineage_status[lineage] = describe_lineage(formation, lineage, targets)
        verify_resource_destination(identity, targets)
        metadata = describe_application_resource(session, service, identity)
        if metadata is None:
            continue
        if metadata.get("unsupported"):
            ambiguous.append(
                {
                    "type": service,
                    "identity": identity,
                    "reason": "owned orphan requires native recovery",
                }
            )
            continue
        if read_resource_lineage(metadata, targets) != lineage:
            ambiguous.append(
                {
                    "type": service,
                    "identity": identity,
                    "reason": "native ownership differs from tagging inventory",
                }
            )
            continue
        applications.append(
            {
                "type": service,
                "identity": identity,
                "lineage": lineage,
                "scope": "stack" if lineage == current else "orphan",
                "metadata": metadata,
            }
        )
    outputs = (
        {
            output["OutputKey"]: output["OutputValue"]
            for output in stack.get("Outputs", [])
        }
        if stack
        else {}
    )
    users = []
    pool_identities = {
        resource["metadata"]["Id"]
        for resource in applications
        if resource["type"] == "cognito-idp"
    }
    if outputs.get("UserPoolId"):
        pool_identities.add(outputs["UserPoolId"])
    directory = session.client("cognito-idp")
    for pool in pool_identities:
        try:
            pool_users = [
                {"pool": pool, "username": user["Username"]}
                for page in directory.get_paginator("list_users").paginate(
                    UserPoolId=pool,
                )
                for user in page["Users"]
            ]
        except directory.exceptions.ResourceNotFoundException:
            # A partially deleted stack can keep outputs for a deleted pool.
            # Confirmed absence is complete; authentication failures still stop.
            continue
        users.extend(pool_users)
    return {
        "account": targets.aws_account,
        "region": targets.aws_region,
        "deployment": targets.deployment,
        "stack": current,
        "stack_status": stack["StackStatus"] if stack else "ABSENT",
        "stack_revision": stack_revision,
        "stack_resources": stack_resources,
        "outputs": outputs,
        "users": users,
        "resources": resources,
        "application_orphans": applications,
        "dynamic_model_secrets": secrets,
        "lineage_status": lineage_status,
        "ambiguous_resources": ambiguous,
        "agentcore_interfaces": agentcore_interfaces,
        "preserved": [
            "CDKToolkit and shared asset bucket/bootstrap copies",
            "shared tracing settings",
            "independent cloud trace/audit retention",
            "external OpenRouter accounts/keys",
            "unrelated resources",
        ],
        "data_loss": "database/checkpoints, application users, model-key copies",
    }


def describe_application_resource(
    session: Any, service: str, identity: str
) -> dict | None:
    if service not in (
        "secretsmanager",
        "cognito-idp",
        "logs",
        "bedrock-agentcore",
        "iam",
        "route53",
    ):
        return {"unsupported": True}
    client = session.client(
        service if service != "bedrock-agentcore" else "bedrock-agentcore-control"
    )
    try:
        if service == "secretsmanager":
            return client.describe_secret(SecretId=identity)
        if service == "cognito-idp":
            pool = client.describe_user_pool(UserPoolId=identity.rsplit("/", 1)[-1])[
                "UserPool"
            ]
            tags = client.list_tags_for_resource(ResourceArn=identity)["Tags"]
            return pool | {
                "Tags": [{"Key": name, "Value": value} for name, value in tags.items()]
            }
        if service == "logs":
            return describe_log_resource(client, identity)
        if service == "bedrock-agentcore":
            return describe_agentcore_resource(client, identity)
        if service == "iam":
            name = identity.rsplit("/", 1)[-1]
            if ":role/" in identity:
                role = client.get_role(RoleName=name)["Role"]
                policies = [
                    policy
                    for page in client.get_paginator(
                        "list_attached_role_policies"
                    ).paginate(RoleName=name)
                    for policy in page["AttachedPolicies"]
                ]
                inline = [
                    policy
                    for page in client.get_paginator("list_role_policies").paginate(
                        RoleName=name
                    )
                    for policy in page["PolicyNames"]
                ]
                profiles = [
                    profile
                    for page in client.get_paginator(
                        "list_instance_profiles_for_role"
                    ).paginate(RoleName=name)
                    for profile in page["InstanceProfiles"]
                ]
                return role | {
                    "policies": policies,
                    "inline_policies": inline,
                    "profiles": profiles,
                }
            if ":instance-profile/" in identity:
                return client.get_instance_profile(InstanceProfileName=name)[
                    "InstanceProfile"
                ]
            return {"unsupported": True}
        if service == "route53":
            zone_id = identity.rsplit("/", 1)[-1]
            zone = client.get_hosted_zone(Id=zone_id)["HostedZone"]
            tags = client.list_tags_for_resource(
                ResourceType="hostedzone", ResourceId=zone_id
            )["ResourceTagSet"]["Tags"]
            records = [
                record
                for page in client.get_paginator("list_resource_record_sets").paginate(
                    HostedZoneId=zone_id
                )
                for record in page["ResourceRecordSets"]
            ]
            return zone | {"Tags": tags, "records": records}
        return {"unsupported": True}
    except client.exceptions.ClientError as error:
        if error.response["Error"]["Code"] in (
            "ResourceNotFoundException",
            "ResourceNotFound",
            "EntityNotFoundException",
            "NoSuchEntity",
            "NoSuchHostedZone",
        ):
            return None
        raise


def present_inventory(inventory: dict) -> None:
    resources = [
        {name: resource[name] for name in ("type", "identity", "lineage", "scope")}
        for resource in inventory["resources"] + inventory["application_orphans"]
    ]
    for resource, summary in zip(
        inventory["resources"] + inventory["application_orphans"],
        resources,
        strict=True,
    ):
        summary["associated_changes"] = {
            name: value
            for name, value in resource["metadata"].items()
            if name
            in (
                "policies",
                "inline_policies",
                "profiles",
                "records",
                "Attachments",
                "RequesterManaged",
                "implicit_disk_changes",
                "IpPermissions",
                "IpPermissionsEgress",
                "subtype",
                "service_managed",
                "resourceArns",
                "deliverySourceName",
                "deliveryDestinationArn",
                "deliveryDestinationConfiguration",
                "workload_identity",
                "runtime_id",
            )
        }
    show_inventory(
        "AWS reviewed deletion inventory",
        {
            name: value
            for name, value in inventory.items()
            if name not in ("resources", "application_orphans")
        }
        | {"resources": resources},
    )


def confirm_data_loss(
    targets: Targets, target: str | None, deployment: str | None, data_loss: str | None
) -> None:
    target = target if target is not None else ask_text("Type the exact AWS account")
    deployment = (
        deployment
        if deployment is not None
        else ask_text("Type the exact deployment name")
    )
    data_loss = (
        data_loss
        if data_loss is not None
        else ask_text(f"Type {DATA_LOSS_CONFIRMATION}")
    )
    if (
        target != targets.aws_account
        or deployment != targets.deployment
        or data_loss != DATA_LOSS_CONFIRMATION
    ):
        raise PermissionError(
            "Exact target, deployment and data-loss confirmations are required"
        )


def delete_model_secrets(store: Any, secrets: list[str], targets: Targets) -> list[str]:
    for identity in secrets:
        try:
            metadata = store.describe_secret(SecretId=identity)
        except store.exceptions.ResourceNotFoundException:
            continue
        if read_tags(metadata).get("Deployment") != targets.deployment:
            raise PermissionError("Model secret ownership changed since review")
        verify_resource_destination(identity, targets)
        if not metadata.get("DeletedDate"):
            store.delete_secret(SecretId=identity, ForceDeleteWithoutRecovery=True)
    pending = set(secrets)
    deadline = time.monotonic() + 60
    while pending and time.monotonic() < deadline:
        for identity in tuple(pending):
            try:
                store.describe_secret(SecretId=identity)
            except store.exceptions.ResourceNotFoundException:
                pending.remove(identity)
        if pending:
            time.sleep(2)
    return sorted(pending)


def delete_network_resource(
    compute: Any,
    resource: dict,
    owned_identities: set[str],
    targets: Targets,
) -> None:
    identity = resource["identity"]
    metadata = resource["metadata"]
    match resource["type"]:
        case "instance":
            verify_instance_disks(
                compute, metadata, resource["reviewed_metadata"], targets
            )
            if metadata["State"]["Name"] != "shutting-down":
                compute.terminate_instances(InstanceIds=[identity])
            compute.get_waiter("instance_terminated").wait(
                InstanceIds=[identity], WaiterConfig={"Delay": 5, "MaxAttempts": 120}
            )
        case "nat_gateway":
            if metadata["State"] != "deleting":
                compute.delete_nat_gateway(NatGatewayId=identity)
            compute.get_waiter("nat_gateway_deleted").wait(
                NatGatewayIds=[identity], WaiterConfig={"Delay": 5, "MaxAttempts": 120}
            )
        case "endpoint":
            response = compute.delete_vpc_endpoints(VpcEndpointIds=[identity])
            if response.get("Unsuccessful"):
                raise RuntimeError("VPC endpoint deletion is incomplete")
        case "interface":
            if (
                metadata.get("RequesterManaged")
                or metadata.get("Attachment")
                or metadata["Status"] != "available"
            ):
                raise PermissionError(
                    "Interface is attached/service-controlled; wait for service cleanup"
                )
            compute.delete_network_interface(NetworkInterfaceId=identity)
        case "route_table":
            for association in metadata.get("Associations", []):
                if association.get("Main"):
                    return
                if association.get("SubnetId") not in owned_identities:
                    raise PermissionError(
                        "Route table has an unreviewed subnet association"
                    )
                compute.disassociate_route_table(
                    AssociationId=association["RouteTableAssociationId"]
                )
            compute.delete_route_table(RouteTableId=identity)
        case "security_group":
            if resource.get("dependency_cycle"):
                raise PermissionError("Group cycle needs reviewed rule cleanup")
            if not read_group_references(metadata) <= read_group_references(
                resource["reviewed_metadata"]
            ):
                raise PermissionError("Group dependencies expanded since review")
            if metadata["GroupName"] != "default":
                compute.delete_security_group(GroupId=identity)
        case "internet_gateway":
            for attachment in metadata.get("Attachments", []):
                if attachment["VpcId"] not in owned_identities:
                    raise PermissionError(
                        "Internet gateway has an unreviewed VPC attachment"
                    )
                compute.detach_internet_gateway(
                    InternetGatewayId=identity, VpcId=attachment["VpcId"]
                )
            compute.delete_internet_gateway(InternetGatewayId=identity)
        case "address":
            if metadata.get("AssociationId"):
                raise PermissionError(
                    "Elastic address remains associated; inspect its service owner"
                )
            compute.release_address(AllocationId=identity)
        case "subnet":
            compute.delete_subnet(SubnetId=identity)
        case "vpc":
            compute.delete_vpc(VpcId=identity)
        case "volume":
            if metadata.get("Attachments"):
                raise PermissionError(
                    "Retained database volume remains attached; never force-detach"
                )
            if metadata["State"] != "deleting":
                compute.get_waiter("volume_available").wait(
                    VolumeIds=[identity], WaiterConfig={"Delay": 2, "MaxAttempts": 60}
                )
                compute.delete_volume(VolumeId=identity)
            compute.get_waiter("volume_deleted").wait(
                VolumeIds=[identity], WaiterConfig={"Delay": 2, "MaxAttempts": 60}
            )


def delete_application_resource(session: Any, resource: dict, targets: Targets) -> None:
    metadata = describe_application_resource(
        session, resource["type"], resource["identity"]
    )
    if metadata is None:
        return
    if read_resource_lineage(metadata, targets) != resource["lineage"]:
        raise PermissionError("Application orphan ownership changed since review")
    match resource["type"]:
        case "secretsmanager":
            if not metadata.get("DeletedDate"):
                session.client("secretsmanager").delete_secret(
                    SecretId=resource["identity"], ForceDeleteWithoutRecovery=True
                )
        case "cognito-idp":
            session.client("cognito-idp").delete_user_pool(UserPoolId=metadata["Id"])
        case "logs":
            client = session.client("logs")
            match metadata["subtype"]:
                case "delivery":
                    if any(
                        metadata.get(name) != resource["metadata"].get(name)
                        for name in ("deliverySourceName", "deliveryDestinationArn")
                    ):
                        raise PermissionError("Log-delivery dependencies changed")
                    client.delete_delivery(id=metadata["id"])
                case "delivery-source":
                    if metadata.get("resourceArns") != resource["metadata"].get(
                        "resourceArns"
                    ):
                        raise PermissionError("Log-delivery source changed")
                    client.delete_delivery_source(name=metadata["name"])
                case "delivery-destination":
                    if metadata.get("deliveryDestinationConfiguration") != resource[
                        "metadata"
                    ].get("deliveryDestinationConfiguration"):
                        raise PermissionError("Log-delivery destination changed")
                    client.delete_delivery_destination(name=metadata["name"])
                case "log_group":
                    client.delete_log_group(logGroupName=metadata["logGroupName"])
        case "bedrock-agentcore":
            client = session.client("bedrock-agentcore-control")
            match metadata["subtype"]:
                case "runtime_endpoint":
                    if metadata["service_managed"]:
                        parent = describe_application_resource(
                            session,
                            "bedrock-agentcore",
                            resource["identity"].split("/runtime-endpoint/", 1)[0],
                        )
                        if parent is not None:
                            raise PermissionError("DEFAULT endpoint has a live runtime")
                        wait_for_agentcore_deletion(session, resource["identity"])
                        return
                    if metadata["status"] != "DELETING":
                        client.delete_agent_runtime_endpoint(
                            agentRuntimeId=metadata["runtime_id"],
                            endpointName=metadata["name"],
                        )
                case "runtime":
                    if metadata["status"] != "DELETING":
                        client.delete_agent_runtime(
                            agentRuntimeId=metadata["runtime_id"]
                        )
                case "workload_identity":
                    for page in client.get_paginator("list_agent_runtimes").paginate():
                        for runtime in page["agentRuntimes"]:
                            deployment = describe_application_resource(
                                session, "bedrock-agentcore", runtime["agentRuntimeArn"]
                            )
                            if (
                                deployment
                                and deployment.get("workload_identity")
                                == resource["identity"]
                            ):
                                raise PermissionError("Workload has a live runtime")
                    client.delete_workload_identity(name=metadata["name"])
            wait_for_agentcore_deletion(session, resource["identity"])
        case "iam":
            client = session.client("iam")
            if "RoleName" in metadata:
                if any(
                    metadata[name] != resource["metadata"][name]
                    for name in ("policies", "inline_policies", "profiles")
                ):
                    raise PermissionError("Role dependencies changed since review")
                name = metadata["RoleName"]
                for profile in metadata["profiles"]:
                    native = client.get_instance_profile(
                        InstanceProfileName=profile["InstanceProfileName"]
                    )["InstanceProfile"]
                    if read_resource_lineage(native, targets) != resource["lineage"]:
                        raise PermissionError(
                            "Role profile lacks verified ownership; recover separately"
                        )
                    client.remove_role_from_instance_profile(
                        InstanceProfileName=profile["InstanceProfileName"],
                        RoleName=name,
                    )
                    client.delete_instance_profile(
                        InstanceProfileName=profile["InstanceProfileName"]
                    )
                for policy in metadata["policies"]:
                    client.detach_role_policy(
                        RoleName=name, PolicyArn=policy["PolicyArn"]
                    )
                for policy in metadata["inline_policies"]:
                    client.delete_role_policy(RoleName=name, PolicyName=policy)
                client.delete_role(RoleName=name)
            else:
                if metadata.get("Roles"):
                    raise PermissionError(
                        "Instance profile has roles; recover its owned role first"
                    )
                client.delete_instance_profile(
                    InstanceProfileName=metadata["InstanceProfileName"]
                )
        case "route53":
            if metadata["records"] != resource["metadata"]["records"]:
                raise PermissionError("Hosted zone records changed since review")
            client = session.client("route53")
            changes = [
                {"Action": "DELETE", "ResourceRecordSet": record}
                for record in metadata["records"]
                if record["Type"] not in ("NS", "SOA")
                or record["Name"] != metadata["Name"]
            ]
            for offset in range(0, len(changes), 100):
                response = client.change_resource_record_sets(
                    HostedZoneId=metadata["Id"],
                    ChangeBatch={"Changes": changes[offset : offset + 100]},
                )
                client.get_waiter("resource_record_sets_changed").wait(
                    Id=response["ChangeInfo"]["Id"],
                    WaiterConfig={"Delay": 2, "MaxAttempts": 90},
                )
            client.delete_hosted_zone(Id=metadata["Id"])


def destroy_aws(
    targets: Targets,
    preview: bool = False,
    confirm_target: str | None = None,
    confirm_deployment: str | None = None,
    confirm_loss: str | None = None,
) -> None:
    session = targets.create_aws_session()
    inventory = discover_inventory(targets, session)
    present_inventory(inventory)
    save_inventory(inventory | {"status": "preview"})
    if inventory["agentcore_interfaces"]:
        show_inventory(
            "AWS AgentCore networking lifecycle",
            {
                **build_agentcore_cleanup_guidance(
                    targets,
                    inventory["agentcore_interfaces"],
                ),
                "policy": "Interface association is not permission to detach or delete",
            },
        )
    if preview:
        return
    confirm_data_loss(targets, confirm_target, confirm_deployment, confirm_loss)
    if inventory[
        "stack_status"
    ] == "DELETE_FAILED" and report_agentcore_cleanup_pending(
        targets, session, inventory
    ):
        raise CleanupPending()
    formation = session.client("cloudformation")
    if inventory["stack"]:
        stack = find_stack(targets, formation, inventory["stack"])
        if stack is None or stack["StackId"] != inventory["stack"]:
            raise PermissionError(
                "Reviewed stack identity changed; rediscover before deletion"
            )
        if stack["StackStatus"] != "DELETE_IN_PROGRESS":
            compute = session.client("ec2")
            for resource in inventory["resources"]:
                if (
                    resource["type"] != "instance"
                    or resource["lineage"] != inventory["stack"]
                ):
                    continue
                instances = list_native_resources(
                    compute,
                    "describe_instances",
                    "Reservations",
                    [{"Name": "instance-id", "Values": [resource["identity"]]}],
                )
                if len(instances) != 1:
                    raise PermissionError("Reviewed stack instance scope changed")
                verify_instance_disks(
                    compute, instances[0], resource["metadata"], targets
                )
            stack = find_stack(targets, formation, inventory["stack"])
            if stack is None:
                raise PermissionError("Reviewed stack disappeared; rediscover")
            revision = capture_stack_revision(formation, stack)
            latest = find_stack(targets, formation, inventory["stack"])
            fields = ("StackStatus", "CreationTime", "LastUpdatedTime", "ChangeSetId")
            if (
                latest is None
                or revision != inventory["stack_revision"]
                or any(latest.get(field) != stack.get(field) for field in fields)
            ):
                show_inventory(
                    "AWS stack scope changed; reconfirmation required",
                    {
                        "reviewed_revision": inventory["stack_revision"],
                        "current_revision": revision,
                    },
                )
                raise PermissionError("Stack revision changed; rediscover/reconfirm")
            formation.delete_stack(StackName=inventory["stack"])
        save_inventory(
            inventory | {"status": "stack deletion pending; retained storage held"},
        )
        show_inventory(
            "AWS deletion progress",
            {
                "stack": inventory["stack"],
                "status": "waiting for confirmed deletion",
                "retained_storage": "Deletion requires stack deletion success",
            },
        )
        # Retained database deletion is fenced behind exact stack deletion success.
        try:
            formation.get_waiter("stack_delete_complete").wait(
                StackName=inventory["stack"],
                WaiterConfig={"Delay": 10, "MaxAttempts": 180},
            )
        except WaiterError as error:
            # Authentication, throttling and unrelated failures remain errors;
            # only a native stack observation can qualify as pending cleanup.
            if (
                error.last_response.get("Stacks")
                and not error.last_response.get("Error")
                and report_agentcore_cleanup_pending(targets, session, inventory)
            ):
                raise CleanupPending() from None
            raise
        save_inventory(
            inventory | {"status": "stack deletion confirmed; owned cleanup pending"},
        )
    pending = []
    current = discover_inventory(targets, session)
    if current["stack"] is not None:
        raise PermissionError(
            "A live application stack remains; preserve retained storage/model keys"
        )
    approved = {
        (resource["type"], resource["identity"], resource["lineage"]): resource
        for resource in inventory["resources"] + inventory["application_orphans"]
    }
    owned_identities = {resource["identity"] for resource in inventory["resources"]}
    networking_fence = any(
        resource["type"] == "bedrock-agentcore"
        and (resource["type"], resource["identity"], resource["lineage"])
        not in approved
        for resource in current["application_orphans"]
    )
    interface_fence = False
    interfaces_verification = False
    vpcs = {
        resource["identity"]
        for resource in inventory["resources"]
        if resource["type"] == "vpc"
    }
    applications = sorted(current["application_orphans"], key=read_application_priority)
    networking = order_network_resources(current["resources"])
    cleanup_fence = networking_fence
    for resource in applications + networking:
        if cleanup_fence:
            break
        if (
            resource["type"],
            resource["identity"],
            resource["lineage"],
        ) not in approved:
            continue
        if current["lineage_status"][resource["lineage"]] not in (
            "ABSENT",
            "DELETE_COMPLETE",
        ):
            pending.append(
                {
                    "identity": resource["identity"],
                    "reason": "stack lineage remains active",
                }
            )
            continue
        try:
            snapshot = approved[
                (resource["type"], resource["identity"], resource["lineage"])
            ]
            if resource in applications:
                delete_application_resource(session, snapshot, targets)
            else:
                if networking_fence:
                    raise PermissionError("Networking awaits AgentCore deletion")
                if resource["type"] in ("security_group", "subnet", "vpc"):
                    if interface_fence:
                        raise PermissionError("Networking awaits service interfaces")
                    if not interfaces_verification:
                        try:
                            wait_for_service_interfaces(session.client("ec2"), vpcs)
                        except TimeoutError:
                            interface_fence = True
                            raise
                        interfaces_verification = True
                operation, field, identity_field = next(
                    (operation, field, identity_field)
                    for kind, operation, field, identity_field in NETWORK_RESOURCES
                    if kind == resource["type"]
                )
                native = list_native_resources(
                    session.client("ec2"),
                    operation,
                    field,
                    [{"Name": "tag:Deployment", "Values": [targets.deployment]}],
                )
                metadata = next(
                    (
                        metadata
                        for metadata in native
                        if metadata[identity_field] == resource["identity"]
                    ),
                    None,
                )
                if metadata is None:
                    continue
                if read_resource_lineage(metadata, targets) != resource["lineage"]:
                    raise PermissionError("Networking ownership changed since review")
                delete_network_resource(
                    session.client("ec2"),
                    resource
                    | {
                        "metadata": metadata,
                        "reviewed_metadata": snapshot["metadata"],
                    },
                    owned_identities,
                    targets,
                )
        except Exception as error:
            # Dependency errors are not permission to force-detach or expand scope.
            pending.append(
                {"identity": resource["identity"], "reason": type(error).__name__}
            )
            if resource["type"] == "bedrock-agentcore":
                networking_fence = True
            # An uncertain service delete must not strip its execution role,
            # identity, logs, or keys. Only discovery/reporting follows failure.
            cleanup_fence = True
    secret_pending = (
        inventory["dynamic_model_secrets"]
        if cleanup_fence
        else delete_model_secrets(
            session.client("secretsmanager"),
            inventory["dynamic_model_secrets"],
            targets,
        )
    )
    final = discover_inventory(targets, session)
    remainders = list_reviewed_remainders(session, inventory)
    remaining = final["resources"] + final["application_orphans"]
    incomplete = bool(
        remaining
        or remainders
        or final["ambiguous_resources"]
        or secret_pending
        or final["dynamic_model_secrets"]
        or final["stack"]
    )
    report = final | {
        "status": "incomplete; wait for service cleanup and rediscover"
        if incomplete
        else "owned deletion confirmed; retained bootstrap preserved",
        "pending_operations": pending,
        "pending_secret_deletions": secret_pending,
        "cleanup_fence": cleanup_fence,
        "reviewed_remainders": remainders,
    }
    save_inventory(report)
    present_inventory(report)
    if incomplete:
        if interface_fence and report_agentcore_cleanup_pending(
            targets,
            session,
            inventory,
        ):
            raise CleanupPending()
        raise RuntimeError(
            "AWS teardown is incomplete; inspect pending resources and rediscover"
        )

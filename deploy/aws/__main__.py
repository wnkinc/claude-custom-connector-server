"""mcp-tools on AWS: the same docker-compose stack as a local deploy, on one EC2 VM.

Ingress (the Cloudflare Tunnel + wildcard DNS) is the shared deploy/cloudflare
stack — `pulumi up` there first, then point `cloudflareStack` here at it. This
program consumes its `tunnelId`/`credsJson` outputs, so switching a deployment
between local and AWS keeps the same domain, tunnel, and credentials.

What this program owns (and `pulumi destroy` removes):
  - an EC2 instance (default: t3.small, 20 GB gp3) that boots docker, clones the
    repo at a pinned ref, renders the root .env, and brings up
    docker-compose.yml + docker-compose.tunnel.yml (the boot script is
    userdata.sh) — behind a security group with zero inbound rules (tunnel +
    SSM agent dial out; admin access is SSM Session Manager)
  - the guardrail's provider on this path: an Amazon Bedrock Guardrail
    (prompt-attack filter only) + an instance role scoped to ApplyGuardrail on
    it. The cloud deploy is always bedrock-screened; the local-model
    (llamafirewall) provider is the local path's business.
  - an SSM SecureString parameter carrying the one boot secret (tunnel
    credentials) — secrets stay out of user-data, which is API-readable

What stays manual (see docs/deploy/aws.md): the Google OAuth client, per-tool
.env files (dropped onto the VM over SSM), and the optional Slack app.

Config (pulumi config set <key> <value>):
  domain               (required)  parent domain, on Cloudflare
  cloudflareStack      (required)  StackReference to deploy/cloudflare, e.g.
                                   organization/mcp-tools-cloudflare/prod
                                   (same backend as this stack)
  tools                default "xmcp,telegram" — comma list of tool profiles
  repoUrl              default: the upstream repo
  repoRef              default "main" — pin a tag/commit for reproducible deploys
  instanceType         default "t3.small" — fits the light tools; lean wants ≥ 8 GB RAM
  volumeGb             default 20 (lean's 13 GB base image wants ≥ 100)
  aws:region           the deploy region (bedrock guardrail lives here too)
"""

from __future__ import annotations

import json
from pathlib import Path

import pulumi
import pulumi_aws as aws

UPSTREAM_REPO = "https://github.com/wnkinc/claude-custom-connector-server.git"

cfg = pulumi.Config()
domain = cfg.require("domain")
cloudflare_stack = cfg.require("cloudflareStack")
tools = [t.strip() for t in (cfg.get("tools") or "xmcp,telegram").split(",") if t.strip()]
repo_url = cfg.get("repoUrl") or UPSTREAM_REPO
repo_ref = cfg.get("repoRef") or "main"
instance_type = cfg.get("instanceType") or "t3.small"
volume_gb = cfg.get_int("volumeGb") or 20

region = aws.get_region().name
stack = pulumi.get_stack()
prefix = f"mcp-tools-{stack}"

# --- ingress: consumed from the shared deploy/cloudflare stack ---------------------
cloudflare = pulumi.StackReference(cloudflare_stack)
tunnel_id = cloudflare.require_output("tunnelId")
creds_json = cloudflare.require_output("credsJson")  # secret in the source stack

# Staged for the VM as a SecureString so the secret rides SSM (KMS-encrypted,
# IAM-gated) instead of instance user-data.
creds_param = aws.ssm.Parameter(
    f"{prefix}-tunnel-creds",
    name=f"/{prefix}/tunnel-creds",
    type="SecureString",
    value=creds_json,
)

# --- guardrail: Bedrock, always -- the cloud deploy's output screen ----------------
withheld = "[guardrail: content withheld -- the screen flagged likely prompt-injection.]"
guardrail = aws.bedrock.Guardrail(
    f"{prefix}-guardrail",
    name=prefix,
    description="mcp-tools output screen: prompt-attack filter only (parity with PromptGuard).",
    blocked_input_messaging=withheld,
    blocked_outputs_messaging=withheld,
    content_policy_config={
        "filters_configs": [
            {"type": "PROMPT_ATTACK", "input_strength": "HIGH", "output_strength": "NONE"}
        ]
    },
)

# --- instance identity: SSM admin + exactly the one boot read + ApplyGuardrail -----
role = aws.iam.Role(
    f"{prefix}-role",
    assume_role_policy=json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ec2.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    ),
)
aws.iam.RolePolicyAttachment(
    f"{prefix}-ssm-core",
    role=role.name,
    policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
)
aws.iam.RolePolicy(
    f"{prefix}-boot-and-guardrail",
    role=role.name,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "ssm:GetParameter",
                    "Resource": creds_param.arn,
                },
                {
                    "Effect": "Allow",
                    "Action": "bedrock:ApplyGuardrail",
                    "Resource": guardrail.guardrail_arn,
                },
            ],
        }
    ),
)
profile = aws.iam.InstanceProfile(f"{prefix}-profile", role=role.name)

# --- the VM ------------------------------------------------------------------------
default_vpc = aws.ec2.get_vpc(default=True)
sg = aws.ec2.SecurityGroup(
    f"{prefix}-sg",
    vpc_id=default_vpc.id,
    description="mcp-tools: zero inbound (tunnel + SSM dial out); all egress",
    egress=[
        {
            "protocol": "-1",
            "from_port": 0,
            "to_port": 0,
            "cidr_blocks": ["0.0.0.0/0"],
            "ipv6_cidr_blocks": ["::/0"],
        }
    ],
)

ami_id = aws.ssm.get_parameter(
    name="/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
).value

BOOT_TEMPLATE = (Path(__file__).parent / "userdata.sh").read_text()


def _user_data(a: dict) -> str:
    subs = {
        "__REPO_URL__": repo_url,
        "__REPO_REF__": repo_ref,
        "__REGION__": region,
        "__DOMAIN__": domain,
        "__PROFILES__": ",".join(tools),
        "__TUNNEL_ID__": a["tunnel_id"],
        "__CREDS_PARAM__": a["creds_param"],
        "__GUARDRAIL_ID__": a["guardrail_id"],
    }
    script = BOOT_TEMPLATE
    for token, value in subs.items():
        script = script.replace(token, value)
    return script


user_data = pulumi.Output.all(
    tunnel_id=tunnel_id,
    creds_param=creds_param.name,
    guardrail_id=guardrail.guardrail_id,
).apply(_user_data)

instance = aws.ec2.Instance(
    f"{prefix}-vm",
    ami=ami_id,
    instance_type=instance_type,
    vpc_security_group_ids=[sg.id],
    iam_instance_profile=profile.name,
    user_data=user_data,
    user_data_replace_on_change=True,  # boot script drift -> fresh VM, never a half-state
    metadata_options={
        "http_tokens": "required",
        # The guardrail container reaches IMDS through the egress proxy (one extra
        # network hop), so the default hop limit of 1 would drop the responses.
        "http_put_response_hop_limit": 2,
    },
    root_block_device={"volume_size": volume_gb, "volume_type": "gp3"},
    tags={"Name": prefix, "project": "mcp-tools"},
)

pulumi.export("instanceId", instance.id)
pulumi.export(
    "connect",
    pulumi.Output.concat("aws ssm start-session --target ", instance.id, " --region ", region),
)
pulumi.export("tunnelId", tunnel_id)
pulumi.export("guardrailId", guardrail.guardrail_id)
pulumi.export("connectorUrls", [f"https://{t}.{domain}/mcp" for t in tools])
pulumi.export("approvalUrl", f"https://approval.{domain}")

import os
from aws_cdk import App
from deploy.aws.stack import ApplicationStack
from scripts.aws.configuration import load_targets


targets = load_targets(
    {
        name: os.environ[name]
        for name in (
            "DEPLOYMENT_NAME",
            "AWS_PROFILE",
            "AWS_ACCOUNT_ID",
            "AWS_REGION",
        )
        if name in os.environ
    }
)
application = App(outdir=os.environ["DEPLOY_CDK_OUTPUT"])
ApplicationStack(application, targets)
application.synth()

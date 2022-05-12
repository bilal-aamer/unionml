"""unionml cli."""

import copy
import json
import os
import sys
from enum import Enum
from pathlib import Path

import click
import typer
import uvicorn
from cookiecutter.main import cookiecutter

from unionml.remote import get_model

sys.path.append(os.curdir)

app = typer.Typer()


IMAGE_PREFIX = "unionml"
FLYTE_SANDBOX_CONTAINER_NAME = "flyte-sandbox"


class AppTemplate(str, Enum):
    basic = "basic"
    basic_aws_lambda = "basic-aws-lambda"


@app.command()
def init(
    app_name: str,
    template: AppTemplate = typer.Option(
        AppTemplate.basic,
        "--template",
        "-t",
        help="template to use for initializing your app.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="overwrites an existing app if flag is set.",
    ),
):
    """Initialize a UnionML project."""
    config = {
        "app_name": app_name,
    }
    cookiecutter(
        str(Path(__file__).parent / "templates" / template.value),
        no_input=True,
        overwrite_if_exists=overwrite,
        extra_context=config,
    )


@app.command()
def deploy(app: str):
    """Deploy model to a Flyte backend."""
    typer.echo(f"[unionml] deploying {app}")
    model = get_model(app)
    model.remote_deploy()


@app.command()
def train(
    app: str,
    inputs: str = typer.Option(None, "--inputs", "-i", help="inputs to pass into training workflow"),
    app_version: str = typer.Option(None, "--app-version", "-v", help="app version"),
):
    """Train a model."""
    typer.echo(f"[unionml] app: {app} - training model")
    model = get_model(app)
    train_inputs = {}
    if inputs:
        train_inputs.update(json.loads(inputs))
    execution = model.remote_train(app_version, **train_inputs)
    model.remote_load(execution)
    assert model.artifact is not None
    typer.echo("[unionml] training completed with model artifacts:")
    typer.echo(f"[unionml] model object: {model.artifact.model_object}")
    typer.echo(f"[unionml] model metrics: {model.artifact.metrics}")


@app.command()
def predict(
    app: str,
    inputs: str = typer.Option(None, "--inputs", "-i", help="inputs"),
    features: Path = typer.Option(None, "--features", "-f", help="generate predictions for this feature"),
    app_version: str = typer.Option(None, "--app-version", "-v", help="app version"),
):
    """Generate prediction."""
    typer.echo(f"[unionml] app: {app} - generating predictions")
    model = get_model(app)

    prediction_inputs = {}
    if inputs:
        prediction_inputs.update(json.loads(inputs))
    elif features:
        with features.open() as f:
            features = json.load(f)
        prediction_inputs.update({"features": model._dataset.get_features(features)})

    predictions = model.remote_predict(app_version, wait=True, **prediction_inputs)
    typer.echo(f"[unionml] predictions: {predictions}")


@app.callback()
def callback():
    """unionml command-line tool."""


def serve_command():
    """Modify the uvicorn.main entrypoint for unionml app serving."""
    fn = copy.deepcopy(uvicorn.main)
    fn.short_help = "Serve an unionml model."
    fn.help = (
        "Serve an unionml model using uvicorn. This command uses the main uvicorn entrypoint with an additional "
        "--model-path argument.\n\nFor more information see: https://www.uvicorn.org/#command-line-options"
    )

    option = click.Option(param_decls=["--model-path"], default=None, help="model path to use for serving", type=Path)
    fn.params.append(option)

    callback = fn.callback

    def custom_callback(**kwargs):
        if os.getenv("UNIONML_MODEL_PATH"):
            typer.echo(
                f"UNIONML_MODEL_PATH environment variable is set to {os.getenv('UNIONML_MODEL_PATH')}. "
                "Please unset this variable before running `unionml serve`.",
                err=True,
            )
            raise typer.Exit(code=1)

        model_path = kwargs.pop("model_path")
        if model_path is not None:
            if not model_path.exists():
                typer.echo(f"Model path {model_path} not found.", err=True)
                raise typer.Exit(code=1)
            os.environ["UNIONML_MODEL_PATH"] = str(model_path)
        return callback(**kwargs)

    fn.callback = custom_callback
    return fn


# convert typer app to click object to define a "serve" command
# that uses the uvicorn.main entrypoint under the hood:
# https://typer.tiangolo.com/tutorial/using-click/#combine-click-and-typer
app = typer.main.get_command(app)
app.add_command(serve_command(), name="serve")


if __name__ == "__main__":
    app()

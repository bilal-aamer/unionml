"""Test UnionML Model object.

Fixtures are defined in conftest.py
"""

import io
import typing
from inspect import signature

import pandas as pd
import pytest
from flytekit import task, workflow
from flytekit.core.python_function_task import PythonFunctionTask
from sklearn.linear_model import LogisticRegression

from unionml.model import BaseHyperparameters, ModelArtifact


def test_model_decorators(model, trainer, predictor, evaluator):
    assert model._trainer == trainer
    assert model._predictor == predictor
    assert model._evaluator == evaluator


def test_model_train_task(model, mock_data):
    train_task = model.train_task()
    reader_ret_type = signature(model._dataset._reader).return_annotation
    eval_ret_type = signature(model._evaluator).return_annotation

    assert isinstance(train_task, PythonFunctionTask)
    assert issubclass(train_task.python_interface.inputs["hyperparameters"], BaseHyperparameters)
    assert train_task.python_interface.inputs["data"] == reader_ret_type
    assert train_task.python_interface.outputs["model_object"].__module__ == "flytekit.types.pickle.pickle"
    assert train_task.python_interface.outputs["metrics"] == typing.Dict[str, eval_ret_type]

    outputs = train_task(
        hyperparameters={"C": 1.0, "max_iter": 1000},
        data=mock_data,
    )

    assert outputs.__class__.__name__ == "ModelArtifact"
    assert isinstance(outputs.model_object, LogisticRegression)
    assert isinstance(outputs.metrics["train"], eval_ret_type)
    assert isinstance(outputs.metrics["test"], eval_ret_type)


@pytest.mark.parametrize("custom_init", [True, False])
def test_model_train(model, custom_init):
    if custom_init:
        # disable default model initialization
        model._init_cls = None

        # define custom init function
        @model.init
        def init(hyperparameters: dict) -> LogisticRegression:
            return LogisticRegression(**hyperparameters)

    model_object, metrics = model.train(
        hyperparameters={"C": 1.0, "max_iter": 1000},
        sample_frac=1.0,
        random_state=123,
    )
    assert isinstance(model_object, LogisticRegression)
    assert isinstance(metrics["train"], float)
    assert isinstance(metrics["test"], float)


@pytest.mark.parametrize(
    "dataset_kwargs",
    [
        {},
        {"loader_kwargs": {"head": 20}},
        {"splitter_kwargs": {"test_size": 0.5, "shuffle": False, "random_state": 54321}},
        {"parser_kwargs": {"features": ["x2", "x3"], "targets": ["y"]}},
    ],
)
def test_model_train_from_data(model, dataset_kwargs):
    model_object, metrics = model.train(
        hyperparameters={"C": 1.0, "max_iter": 1000},
        sample_frac=1.0,
        random_state=123,
        **dataset_kwargs,
    )
    assert isinstance(model_object, LogisticRegression)
    assert isinstance(metrics["train"], float)
    assert isinstance(metrics["test"], float)


def test_model_predict_task(model, mock_data):
    predict_task = model.predict_task()

    assert isinstance(predict_task, PythonFunctionTask)
    assert predict_task.python_interface.inputs["model_object"].__module__ == "flytekit.types.pickle.pickle"
    assert predict_task.python_interface.outputs["o0"] == signature(model._predictor).return_annotation

    model_object = LogisticRegression().fit(mock_data[["x"]], mock_data["y"])
    predictions = predict_task(model_object=model_object, data=mock_data[["x"]])

    model.artifact = ModelArtifact(model_object)
    alt_predictions = model.predict(features=mock_data[["x"]])

    assert all(isinstance(x, float) for x in predictions)
    assert predictions == alt_predictions


def test_model_predict_from_features_task(model, mock_data):
    predict_from_features_task = model.predict_from_features_task()

    assert isinstance(predict_from_features_task, PythonFunctionTask)
    assert (
        predict_from_features_task.python_interface.inputs["model_object"].__module__ == "flytekit.types.pickle.pickle"
    )
    assert (
        predict_from_features_task.python_interface.inputs["features"]
        == signature(model._dataset._reader).return_annotation
    )
    assert predict_from_features_task.python_interface.outputs["o0"] == signature(model._predictor).return_annotation

    predictions = predict_from_features_task(
        model_object=LogisticRegression().fit(mock_data[["x"]], mock_data["y"]),
        features=mock_data[["x"]],
    )
    assert all(isinstance(x, float) for x in predictions)


def test_model_saver_and_loader_filepath(model, tmp_path):
    model_path = tmp_path / "model.joblib"
    model_obj, _ = model.train(hyperparameters={"C": 1.0, "max_iter": 1000}, sample_frac=1.0, random_state=42)
    output_path, *_ = model.save(model_path)

    assert output_path == str(model_path)

    loaded_model_obj = model.load(output_path)
    assert model_obj.get_params() == loaded_model_obj.get_params()


def test_model_saver_and_loader_fileobj(model):
    fileobj = io.BytesIO()
    model_obj, _ = model.train(hyperparameters={"C": 1.0, "max_iter": 1000}, sample_frac=1.0, random_state=42)
    model.save(fileobj)
    loaded_model_obj = model.load(fileobj)
    assert model_obj.get_params() == loaded_model_obj.get_params()


def test_model_train_task_in_flyte_workflow(model, mock_data):
    """Test that the unionml.Model-derived training task can be used in regular Flyte workflows."""

    ModelInternals = typing.NamedTuple("ModelInternals", [("coef", typing.List[float]), ("intercept", float)])

    train_task = model.train_task()

    @task
    def get_model_internals(model_object: LogisticRegression) -> ModelInternals:
        """Task that gets coefficients and biases of the model."""
        return ModelInternals(coef=model_object.coef_[0].tolist(), intercept=model_object.intercept_.tolist()[0])

    @workflow
    def wf(data: pd.DataFrame) -> ModelInternals:
        model_artifact = train_task(
            hyperparameters={"C": 1.0, "max_iter": 1000},
            data=data,
            loader_kwargs={},
            splitter_kwargs={},
            parser_kwargs={},
        )
        return get_model_internals(model_object=model_artifact.model_object)

    output = wf(data=mock_data)
    assert isinstance(output.coef, list)
    assert all(isinstance(x, float) for x in output.coef)
    assert isinstance(output.intercept, float)


def test_model_predict_task_in_flyte_workflow(model, mock_data):
    """Test that the unionml.Model-derived prediction task can be used in regular Flyte workflows."""
    model_obj = LogisticRegression()
    model_obj.fit(mock_data[["x", "x2", "x3"]], mock_data["y"])

    predict_task = model.predict_task()

    @task
    def normalize_predictions(predictions: typing.List[float]) -> typing.List[float]:
        """Task that normalizes predictions."""
        s = pd.Series(predictions)
        return (s - s.mean() / s.std()).tolist()

    @workflow
    def wf(model_obj: LogisticRegression, features: pd.DataFrame) -> typing.List[float]:
        predictions = predict_task(model_object=model_obj, data=features)
        return normalize_predictions(predictions=predictions)

    normalized_predictions = wf(model_obj=model_obj, features=mock_data[["x", "x2", "x3"]])

    assert all(isinstance(x, float) for x in normalized_predictions)
    assert any(x < 0 for x in normalized_predictions)
    assert any(x > 0 for x in normalized_predictions)

from random import Random
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from dynamicprompts.constants import DEFAULT_RANDOM
from dynamicprompts.enums import SamplingMethod
from dynamicprompts.generators import RandomPromptGenerator
from dynamicprompts.wildcards import WildcardManager

from sd_dynamic_prompts.generator_builder import GeneratorBuilder


@pytest.fixture
def manager(tmp_path):
    (tmp_path / "color.txt").write_text("1::A\n2::B\n4::C\n", encoding="utf-8")
    return WildcardManager(tmp_path)


@pytest.mark.parametrize("label,method", [
    ("Random", SamplingMethod.RANDOM),
    ("Decay Random", SamplingMethod.DECAY_RANDOM),
])
def test_builder_passes_sampling_method(manager, label, method):
    with patch("sd_dynamic_prompts.generator_builder.RandomPromptGenerator", wraps=RandomPromptGenerator) as create:
        generator = GeneratorBuilder(manager).set_sampling_method(label).create_generator()
        assert create.call_args.kwargs["default_sampling_method"] == method
        assert generator._context.default_sampling_method == method


def test_default_builder_matches_existing_random(manager):
    builder = GeneratorBuilder(manager).set_seed(12)
    actual = builder.create_generator()
    expected = RandomPromptGenerator(manager, seed=12)
    for template in ["__color__", "{A|B|C}", "{2$$A|B|C}", "__@color__"]:
        assert actual.generate(template, 15) == expected.generate(template, 15)
    assert actual._context.rand.getstate() == expected._context.rand.getstate()


@pytest.mark.parametrize("label", ["Random", "Decay Random"])
@pytest.mark.parametrize("unlink", [False, True])
def test_unlink_keeps_rng_selection(manager, label, unlink):
    generator = (GeneratorBuilder(manager).set_seed(12).set_sampling_method(label)
                 .set_unlink_seed_from_prompt(unlink).create_generator())
    assert (generator._context.rand is DEFAULT_RANDOM) is unlink


@pytest.mark.parametrize("label", ["Random", "Decay Random"])
def test_combinatorial_ignores_random_method(manager, label):
    generator = (GeneratorBuilder(manager).set_sampling_method(label)
                 .set_is_combinatorial(True, 1).create_generator())
    assert generator.generate("{A|B|C}", 3) == ["A", "B", "C"]


@pytest.mark.parametrize("label", ["Random", "Decay Random"])
def test_jinja_keeps_existing_route(manager, label):
    p = Mock(all_prompts=[], all_negative_prompts=[], prompt="", negative_prompt="")
    generator = (GeneratorBuilder(manager).set_sampling_method(label).set_context(p)
                 .set_is_jinja_template(True).create_generator())
    assert generator.generate("{% prompt %}literal{% endprompt %}", 2) == ["literal", "literal"]
    assert generator._generators["random"]._context.default_sampling_method == SamplingMethod.RANDOM


def test_ui_defaults_to_random(monkeypatch_webui, monkeypatch):
    from sd_dynamic_prompts import dynamic_prompting as dp
    component = SimpleNamespace()
    radio = Mock(return_value=component)
    monkeypatch.setattr(dp.gr, "Radio", radio, raising=False)
    monkeypatch.setattr(dp, "_get_install_error_message", lambda: None)
    controls = dp.Script().ui(False)
    assert controls[-1] is component
    assert radio.call_args.kwargs["choices"] == ["Random", "Decay Random"]
    assert radio.call_args.kwargs["value"] == "Random"
    assert radio.call_args.kwargs["elem_id"] == "sddp-sampling-method"


@pytest.mark.parametrize("fixed,unlink", [(False, False), (True, False), (False, True)])
def test_process_generate_lifetime_and_batch_grid(
    monkeypatch_webui, monkeypatch, processing, manager, fixed, unlink,
):
    from sd_dynamic_prompts import dynamic_prompting as dp
    script = dp.Script()
    script._wildcard_manager = manager
    actual_generate = dp.generate_prompts
    generators = []
    draws = []

    def generate(**kwargs):
        generators.append(kwargs["prompt_generator"])
        assert kwargs["num_prompts"] == 20
        return actual_generate(**kwargs)

    def choices(rng, population, *, weights, k):
        assert k == 1
        draws.append((rng, list(weights)))
        return [1]

    monkeypatch.setattr(dp, "generate_prompts", generate)
    monkeypatch.setattr(Random, "choices", choices)
    args = {
        "is_enabled": True, "is_combinatorial": False, "combinatorial_batches": 1,
        "is_magic_prompt": False, "is_feeling_lucky": False, "is_attention_grabber": False,
        "min_attention": 0, "max_attention": 1, "magic_prompt_length": 0, "magic_temp_value": 1,
        "use_fixed_seed": fixed, "unlink_seed_from_prompt": unlink, "disable_negative_prompt": False,
        "enable_jinja_templates": False, "no_image_generation": False, "max_generations": 0,
        "magic_model": None, "magic_blocklist_regex": None, "sampling_method": "Decay Random",
    }
    for _ in range(2):
        processing.batch_size = 4
        processing.n_iter = 5
        processing.set_prompt_for_test("__color__, __color__")
        processing.set_negative_prompt_for_test("ugly")
        script.process(processing, **args)
        assert processing.all_prompts == ["B, B"] * 20
        assert processing.main_prompt == "B, B"
        assert processing.main_negative_prompt == "ugly"
        assert processing.n_iter == 5
        if not unlink:
            assert len(processing.all_seeds) == 20
    assert generators[0] is not generators[1]
    assert generators[0]._context.default_sampler is not generators[1]._context.default_sampler
    assert len(draws) == 80
    assert draws[0][1] == draws[40][1] == [1, 2, 4]
    assert draws[1][1] == draws[39][1] == draws[41][1] == [1, 0.8, 4]
    # All 20 images (40 wildcard evaluations) in each Generate use one RNG/context.
    assert all(rng is generators[0]._context.rand for rng, _ in draws[:40])
    assert all(rng is generators[1]._context.rand for rng, _ in draws[40:])

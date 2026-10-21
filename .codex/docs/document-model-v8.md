# Document Model v8

[Полный русский перевод](document-model-v8.ru.md)

[Русский обзор](#русский-обзор) · [English reference](#english-reference)

## Русский обзор

### Документ v8: параметры и перестроение

Исторический контракт v8 ввёл именованные параметры, привязки и атомарное перестроение через `create_model_parameters`, `bind_model_parameters` и `set_model_parameter`. Он также описывает перестроение элементов, зависящих от топологии. Текущая версия документа — v12; используйте этот текст вместе с последующими версиями, а не как полный перечень текущих ограничений. Перед выбором рёбер инспектируйте актуальную топологию.

Это обзор, не полный перевод. Полные схемы, примеры и подробности контракта приведены на английском ниже.

## English reference

> **Versioned contract (8), not the current schema.** This document records the behavior introduced with Document v8. Current saved documents use **v12**; the project envelope remains v1. Read the [current documentation map](../../docs/README.md) and subsequent version contracts before interpreting introduction-era limits as current restrictions. The historical command/schema examples below are retained for migration and compatibility work.


Document v8 extends the v7 object graph with top-level named parameters and
expression bindings. The project envelope remains version 1. Migration from
document v7 initializes `model_parameters` and `parameter_bindings` to empty
collections; project identity, objects, history and revision are preserved.

## Named parameters and expressions

`model_parameters` maps identifier names to finite numeric `{value, unit}`
records. Supported metadata units are `mm`, `deg`, and `unitless`; expressions
operate on the stored numeric values. Bindings are `{object_id, path,
expression}` records that point to a numeric feature parameter, sketch offset,
or supported numeric sketch-element property. A target may have one binding.

Expressions accept named parameters, numeric literals, parentheses, unary
signs, `+`, `-`, `*`, `/`, and `min()` with 2 to 8 numeric arguments. Evaluation
is bounded and rejects unknown names, calls other than `min`, attributes,
non-finite values, and division by zero.

`create_model_parameters` and `bind_model_parameters` validate batches before
committing them. `set_model_parameter` evaluates every dependent expression,
rebuilds affected editable sketches and feature chains in a staged document,
validates the result, then commits it atomically. A failed expression, kernel
operation, document validation, or save leaves the prior document unchanged.
The Geometry inspector shows named values and uses the Agent API command path
for edits.

Supported rebuild paths include boxes, sketch profiles, fused pads and ribs,
sketch pockets, bosses, holes and counterbores/countersinks, body patterns,
fillets, chamfers, and downstream features in document order. Add a rebuild
path before binding a feature input whose operation is not covered. `min()` is
useful for keeping a fillet below a geometric clearance while preserving the
nominal radius whenever the clearance permits.

## Rebuilding topology-selected edge features

Topology `edge_keys` remain current-geometry fingerprints, not permanent edge
names. Edge features also retain compact geometric references. During an
upstream rebuild CADYX resolves the current edge from those references and
stores the refreshed `edge_indices` alongside the regenerated B-rep. Exact
semantic-key matches are preferred; geometric fallback uses curve type,
orientation, length, and proximity, and fails when a unique edge cannot be
identified. Inspect topology again when authoring a new edge feature.

`fillet_edges` accepts multiple unique edge indices for one source body and
one radius. Prefer a single operation when the selected edges share the same
effective radius, are available on the same pre-fillet body, and the combined
kernel operation succeeds. Keep separate features when geometry or radius
expressions differ, or when the combined operation is not valid.

`merge_consecutive_fillet_features` atomically replaces a linear chain of two
or more fillet features with one feature on their common first source body.
The chain must use one numeric radius and, when bound, the same radius
expression. Every saved edge reference must resolve uniquely on that source;
branching dependencies from intermediate bodies are rejected. Dependents of
the final fillet body are reparented and rebuilt in the staged document. A
failed combined fillet or dependent rebuild leaves the saved document
unchanged.

`insert_fillet_before_feature` atomically inserts a boss-root fillet before a
downstream sketch pocket, reparents dependent features, reorders the feature
chain, and rebuilds its descendants. It selects the boss's full analytic base
circle before the pocket interrupts that circle. This is a general editable
feature-chain operation, not a geometry import.

## Persisted validation registry

`cadyx/persisted_schemas.py` is the canonical registry for object kinds and
operation-specific feature validators. Register a new feature operation there
alongside its Command System emitter; literal feature records are checked for
registration by `tests/test_persisted_schemas.py`. Operations historically lacking
additional field restrictions and unknown extension operations retain that
permissive behavior; registration does not imply geometry certification.
`Document.validate` owns unique typed identities, reciprocal body/feature
ownership and parameter bindings; registered feature validators resolve source
references against its shared object index. Sketch fields and constraints still
use the existing batch `validate_sketches` validator. Kernel checks remain separate.

Sketch constraints are solved by the bounded solver documented in [Document
Model v7 constraint semantics](document-model-v7.md#constraint-semantics); a
`fixed` constraint stores its captured point position, and named-parameter-bound
coordinates remain fixed solver inputs. This adds no document version or
migration; legacy checked constraints remain valid and are solved when edited.

The document version remains v8; sequential copy-only migrations run before
validation, including all supported v1–v7 upgrades to v8.

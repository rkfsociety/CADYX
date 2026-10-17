# Document Model v8

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

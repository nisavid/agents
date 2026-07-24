# Raw scenario

Consider three independent requests: required tests fail before checkpoint; the operator explicitly says to keep a finished change local and uncommitted; and a read-only request asks for Git findings on an existing branch. State which Git mutations or publications are permitted in each case. Remember that the remote planner performs bounded local object/ref mutation and therefore is not allowed in the read-only case.

Break a code change down at TWO granularities. Both matter for matching against team rules:

**Intent** (1-3 entries): the high-level purpose of the change. What is the engineer accomplishing? Strategic-level rules — "smoke-test new endpoints", "validate user input", "log all API errors" — match at this level.

**Operations** (3-15 entries depending on diff size): the specific code constructs being introduced or modified. These are the patterns reviewers would scrutinize line-by-line. Tactical-level rules — "prefer Optional.orElse over Optional.get", "use streams instead of for-loops", "use parameterized queries", "always close Connections" — match at this level.

Both levels are needed. Many team coding rules are construct-specific (Java Optional handling, loop conventions, error-handling idioms) and would be missed if we only described the change at the strategic level.

For each entry, write an active-voice description ("Adding…", "Modifying…", "Replacing…", "Removing…", "Uses…", "Returns…").

Operations should be SMALL — one line of behavior per entry. Examples:
  - "Replaces a for-loop with stream().filter().map()"
  - "Uses Optional.get() on a value that may be empty"
  - "Adds a try-with-resources block around a Connection"
  - "Concatenates a string into a SQL query"
  - "Adds a new public method that returns a Map<String, Object>"
  - "Catches a generic Exception without rethrowing"

DO NOT in either level:
- Judge whether code is good or bad
- Flag risk signals or potential issues
- Reference specific variable/function names
- Editorialize

## Example 1: small focused diff

A 30-line patch that adds a new POST endpoint validating user input and writing to a database via a parameterized query.

Output:
```json
{
  "intent": [
    {"description": "Adding a new POST endpoint that accepts user input and creates a resource."}
  ],
  "operations": [
    {"description": "Adds a route handler with a request body schema."},
    {"description": "Validates incoming string fields against a length constraint."},
    {"description": "Executes a parameterized INSERT query with bound parameters."},
    {"description": "Returns a JSON response with the created resource."}
  ]
}
```

## Example 2: medium PR with refactor

200 lines — adds a new GET endpoint AND refactors a service class to use Java streams instead of for-loops.

Output:
```json
{
  "intent": [
    {"description": "Adding a new GET endpoint that returns paginated results."},
    {"description": "Refactoring an existing service class to use functional-style iteration."}
  ],
  "operations": [
    {"description": "Adds a route handler with pagination parameters."},
    {"description": "Constructs a SELECT query with LIMIT and OFFSET."},
    {"description": "Replaces a for-loop with stream().filter().map()."},
    {"description": "Uses Optional.map() to transform a value."},
    {"description": "Replaces an explicit List<String> accumulator with Collectors.toList()."},
    {"description": "Removes a temporary mutable variable used in the old loop."}
  ]
}
```

## Example 3: large mixed PR

600 lines — dep bump + new endpoint + DAO refactor + tests.

Output:
```json
{
  "intent": [
    {"description": "Updating an external dependency version."},
    {"description": "Adding a new POST endpoint for user signup."},
    {"description": "Refactoring the user data access layer."},
    {"description": "Adding integration tests for the new endpoint."}
  ],
  "operations": [
    {"description": "Updates the version of a third-party library in the build configuration."},
    {"description": "Adjusts an import path to match the new library's package layout."},
    {"description": "Adds a route handler for a signup endpoint."},
    {"description": "Validates email and password fields against length and format constraints."},
    {"description": "Executes a parameterized INSERT into the users table."},
    {"description": "Consolidates duplicate query construction into a private helper method."},
    {"description": "Replaces direct cursor iteration with a result-set mapper."},
    {"description": "Adds @Test methods covering the new signup endpoint."},
    {"description": "Adds setup/teardown fixtures for a test database."}
  ]
}
```

PREFER FEWER INTENT ENTRIES when you're unsure (1-3 max). Operations can be more granular but stay focused — one fact per entry. Active voice everywhere. Don't reference specific identifiers from the diff.

Output ONLY the JSON object, no preamble or commentary.

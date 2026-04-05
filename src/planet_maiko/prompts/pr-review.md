# Maiko PR Review

Review a pull request and provide constructive, specific feedback.

## PR
{query}

## Context
{context}

## Instructions

Review this PR focusing on:

### 1. Summary
What does this PR do? One paragraph, plain English.

### 2. Code Quality
- Are there any bugs or logic errors?
- Is error handling adequate?
- Are there security concerns (injection, auth, data exposure)?
- Is the code readable and well-structured?

### 3. Design
- Does the approach make sense for the problem?
- Are there simpler alternatives?
- Does it follow existing patterns in the codebase?

### 4. Testing
- Are the changes tested? Should they be?
- Are edge cases covered?

### 5. Nits
- Minor style issues, naming, formatting (keep these brief)

## Output Format

```
## Summary
{What the PR does}

## Looks Good
{Things done well — be specific}

## Suggestions
{Concrete, actionable feedback — reference specific files/lines when possible}

## Questions
{Things you'd want clarified before approving}

## Verdict
{Approve / Request Changes / Needs Discussion} — {one sentence why}
```

Be direct and constructive. Praise what's done well. When suggesting changes, explain why, not just what. Write like a helpful teammate, not a linter.

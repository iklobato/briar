"""Prompt text for the 2.0 agents (decisions C21, A1, A3, A12).

`roles/` holds a role's base prompt, `tasks/` the task prompt a stage adds
(the 1.x pr-fixer, CI fixer and conflict resolver became engineer tasks),
`rules/` the shared rules included in every prompt they apply to. Spec 003
assembles them; until then nothing reads these files at run time.
"""

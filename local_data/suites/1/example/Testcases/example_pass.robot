*** Settings ***
Documentation    A minimal suite so a fresh install has something to run.
...              Only files inside a folder named "Testcases" appear in the
...              suite-path picker — resource and keyword files cannot run alone.

*** Test Cases ***
Arithmetic Still Works
    ${sum} =    Evaluate    2 + 2
    Should Be Equal As Integers    ${sum}    4

Environment Variable Is Injected
    [Documentation]    BRACE passes RUN_ID and BSS_ENV into every run.
    Log    Running as ${BSS_ENV}

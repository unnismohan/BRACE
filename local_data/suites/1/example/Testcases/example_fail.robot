*** Settings ***
Documentation    Deliberately fails, so you can see the inline failure summary,
...              the AI debug assistant, and "Re-run Failed" without breaking
...              anything real.

*** Test Cases ***
This One Fails On Purpose
    Deep Keyword

*** Keywords ***
Deep Keyword
    Inner Check

Inner Check
    [Documentation]    BRACE reports the innermost failing keyword, not this one.
    Should Be Equal    ${1}    ${2}    Expected the values to match

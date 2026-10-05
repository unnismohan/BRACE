"""Run inside the built production image. Checks Chrome and real Robot + rebot."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    assert os.getuid() != 0, "Production image must run as non-root"
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service

    options = webdriver.ChromeOptions()
    options.binary_location = os.environ.get("CHROME_BIN", "/opt/chromium/chrome")
    options.add_argument("--disable-dev-shm-usage")
    # Existing deployment requires Chrome without sandbox; keep flag explicit.
    options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(
        service=Service(os.environ.get("CHROMEDRIVER", "/opt/chromium/chromedriver")),
        options=options,
    )
    try:
        driver.get(
            'data:text/html,<title>BRACE validation</title><h1 id="ok">Ready</h1>'
        )
        assert driver.title == "BRACE validation"
        assert driver.find_element("id", "ok").text == "Ready"
        chrome = driver.capabilities["browserVersion"]
    finally:
        driver.quit()
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        suite = root / "smoke.robot"
        suite.write_text("""*** Settings ***
Library    SeleniumLibrary
*** Test Cases ***
Production Chrome integration
    ${options}=    Evaluate    selenium.webdriver.ChromeOptions()    modules=selenium.webdriver
    Call Method    ${options}    add_argument    --no-sandbox
    Call Method    ${options}    add_argument    --disable-dev-shm-usage
    ${options.binary_location}=    Set Variable    %{CHROME_BIN=/opt/chromium/chrome}
    ${service}=    Evaluate    selenium.webdriver.chrome.service.Service(os.environ['CHROMEDRIVER'])    modules=selenium.webdriver.chrome.service,os
    Create Webdriver    Chrome    service=${service}    options=${options}
    Go To    data:text/html,<title>BRACE validation</title><h1 id="ok">Ready</h1>
    Title Should Be    BRACE validation
    Element Text Should Be    id=ok    Ready
    [Teardown]    Close All Browsers
""")
        subprocess.run(
            [sys.executable, "-m", "robot", "--outputdir", directory, str(suite)],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "robot.rebot",
                "--outputdir",
                str(root / "combined"),
                str(root / "output.xml"),
            ],
            check=True,
        )
        assert (root / "combined" / "report.html").is_file()
    print(
        json.dumps(
            {
                "status": "passed",
                "python": sys.version.split()[0],
                "chrome": chrome,
                "uid": os.getuid(),
            }
        )
    )


if __name__ == "__main__":
    main()

"""One bounded release-account model check; emits no key or generated content."""
import json
import os

from openai import OpenAI
from lecturesift.assistant_catalog import MODEL


def main():
    if not os.getenv('OPENAI_API_KEY'):
        raise SystemExit('Release model key is unavailable in this environment')
    try:
        with OpenAI(timeout=30, max_retries=0) as client:
            result = client.responses.create(model=MODEL, input='Reply with OK.', store=False,
                                             reasoning={'effort':'none'}, max_output_tokens=16)
        usage = result.usage
        if result.status != 'completed' or not result.output_text or usage is None:
            raise RuntimeError('Incomplete provider result')
        print(json.dumps({'model': MODEL, 'status':'available',
                          'input_tokens':usage.input_tokens, 'output_tokens':usage.output_tokens}))
    except Exception:
        raise SystemExit('Release model access check failed; chat remains disabled') from None


if __name__ == '__main__':
    main()

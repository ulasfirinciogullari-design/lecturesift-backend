"""One bounded release-account model check; emits no key or generated content."""
import json
import os
import argparse

from openai import OpenAI
from lecturesift.assistant_catalog import MODEL, IMAGE_MODEL


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', action='store_true', help='Verify one priced medium square image instead of chat')
    args = parser.parse_args()
    if not os.getenv('OPENAI_API_KEY'):
        raise SystemExit('Release model key is unavailable in this environment')
    try:
        with OpenAI(timeout=85 if args.image else 30, max_retries=0) as client:
            if args.image:
                result = client.images.generate(model=IMAGE_MODEL, prompt='A simple blue water droplet educational icon.',
                                                n=1, quality='medium', size='1024x1024',
                                                output_format='jpeg', output_compression=85, moderation='auto')
                if len(result.data or []) != 1 or not result.data[0].b64_json:
                    raise RuntimeError('Incomplete image result')
            else:
                result = client.responses.create(model=MODEL, input='Reply with OK.', store=False,
                                                 reasoning={'effort':'none'}, max_output_tokens=16)
                if result.status != 'completed' or not result.output_text:
                    raise RuntimeError('Incomplete provider result')
        usage = result.usage
        if usage is None:
            raise RuntimeError('Incomplete provider result')
        print(json.dumps({'model': IMAGE_MODEL if args.image else MODEL, 'status':'available',
                          'input_tokens':usage.input_tokens, 'output_tokens':usage.output_tokens}))
    except Exception:
        raise SystemExit('Release model access check failed; chat remains disabled') from None


if __name__ == '__main__':
    main()

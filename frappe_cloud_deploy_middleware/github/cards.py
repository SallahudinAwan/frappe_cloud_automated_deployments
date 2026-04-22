def github_pr_card(repo_name, status_text, pr_title, from_branch, to_branch, actor, time, pr_url):
    card = {
        "cardsV2": [
            {
                "cardId": "github-pr-start",
                "card": {
                    "header": {
                        "title": f"{repo_name}",
                        "subtitle": status_text,
                        "imageUrl": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Title", "text": f"{pr_title}"}},
                                {"decoratedText": {"topLabel": "From to Branch", "text": f"{from_branch} to {to_branch}"}},
                                {"decoratedText": {"topLabel": "Actor", "text": actor}},
                                {"decoratedText": {"topLabel": "Time", "text": time}},
                            ]
                        },
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Link", "text": ""}},
                                {
                                    "buttonList": {
                                        "buttons": [
                                            {"text": pr_url, "onClick": {"openLink": {"url": pr_url}}},
                                        ]
                                    }
                                },
                            ]
                        },
                    ],
                },
            }
        ]
    }

    return card


def github_workflow_card(repo_name, status_text, pr_title, from_branch, to_branch, actor, time, pr_url):
    card = {
        "cardsV2": [
            {
                "cardId": "github-pr-start",
                "card": {
                    "header": {
                        "title": f"{repo_name}",
                        "subtitle": status_text,
                        "imageUrl": "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png",
                        "imageType": "CIRCLE",
                    },
                    "sections": [
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Title", "text": f"{pr_title}"}},
                                {"decoratedText": {"topLabel": "From to Branch", "text": f"{from_branch} to {to_branch}"}},
                                {"decoratedText": {"topLabel": "Actor", "text": actor}},
                                {"decoratedText": {"topLabel": "Time", "text": time}},
                            ]
                        },
                        {
                            "widgets": [
                                {"decoratedText": {"topLabel": "PR Link", "text": ""}},
                                {
                                    "buttonList": {
                                        "buttons": [
                                            {"text": pr_url, "onClick": {"openLink": {"url": pr_url}}},
                                        ]
                                    }
                                },
                            ]
                        },
                    ],
                },
            }
        ]
    }

    return card


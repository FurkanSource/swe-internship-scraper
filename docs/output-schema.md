# Output schema

Public JSON exports emit `schema_version: 2`. Readers continue to accept v1.

```json
{
  "schema_version": 2,
  "generated_at": "2026-09-21T12:00:00+00:00",
  "job_count": 1,
  "error_count": 0,
  "providers": ["greenhouse"],
  "jobs": [
    {
      "id": "greenhouse:example:123",
      "company": "Example",
      "title": "Software Engineering Intern - Summer 2027",
      "application_url": "https://job-boards.greenhouse.io/example/jobs/123",
      "provider": "greenhouse",
      "source_job_id": "123",
      "locations": ["New York, NY"],
      "posted_at": "2026-09-20T12:00:00+00:00",
      "description": "",
      "remote": false,
      "metadata": {},
      "sources": [
        {
          "provider": "greenhouse",
          "source_job_id": "123",
          "application_url": "https://job-boards.greenhouse.io/example/jobs/123"
        }
      ],
      "merge_evidence": []
    }
  ],
  "errors": []
}
```

Unknown dates remain empty strings. Provider failures include provider, company, slug, and error. `sources` always includes the primary source. `merge_evidence` is empty for an unmerged record and includes `reason`, `confidence`, and the merged `source` otherwise.

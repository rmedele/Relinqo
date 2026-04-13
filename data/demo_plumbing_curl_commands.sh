#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"

curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Megan Foster","sender_email":"megan.foster@example.com","subject":"Emergency plumber needed tonight","body":"We have a burst pipe flooding the laundry room and water is spreading fast. Please call ASAP at 780-555-0101. We are in Edmonton near Windermere."}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Kyle Benson","sender_email":"kyle.benson@example.com","subject":"No hot water and active leak","body":"Our hot water tank is leaking and there is water pooling in the utility room. This feels urgent. Please reach me at 587-555-0144 in St. Albert."}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Rita James","sender_email":"rita.james@example.com","subject":"Sewer backup emergency","body":"We have sewage backing up in the basement bathroom. Need emergency help immediately. Call 780-555-0188. Property is in Sherwood Park."}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Daniel Wu","sender_email":"daniel.wu@example.com","subject":"Quote for sump pump replacement","body":"Hi, I want an estimate for replacing a sump pump in my home. Can you send pricing and availability for a job in Edmonton?"}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Alyssa Reed","sender_email":"alyssa.reed@example.com","subject":"Need quote for rough-in plumbing","body":"We are finishing a basement and need a consultation plus quote for rough-in plumbing for a bathroom. The property is near Leduc."}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Harpreet Gill","sender_email":"harpreet.gill@example.com","subject":"Pricing request for water softener install","body":"Please provide pricing for installing a new water softener and connecting it properly. We would like a quote for a home in Beaumont."}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Laura Kim","sender_email":"laura.kim@example.com","subject":"Question about service area","body":"Hi, do you service acreages just outside Spruce Grove, and do you handle seasonal plumbing inspections?"}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Noah Patel","sender_email":"noah.patel@example.com","subject":"General question about booking","body":"I am planning some minor plumbing updates next month and wanted to know how far in advance I should book."}'

echo
curl -X POST "$BASE_URL/ingest-lead" -H 'Content-Type: application/json' -d '{"source":"demo_plumbing","sender_name":"Growth Team","sender_email":"growth-hacks@example.com","subject":"Double your plumbing leads with SEO backlinks","body":"We sell backlinks, guest posts, SEO rankings, casino traffic and marketing packages for local plumbing businesses."}'
echo

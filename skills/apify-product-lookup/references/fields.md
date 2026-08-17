# Field map for E-commerce Scraping Tool output

Every shape below was observed in live runs against Amazon and Walmart, not inferred
from documentation. Where the two disagree, both forms are listed, because a reader
that handles only one breaks on the other retailer.

## Contents

- [Top level](#top-level)
- [Inside additionalProperties](#inside-additionalproperties)
- [Per-retailer differences](#per-retailer-differences)
- [Reading each field safely](#reading-each-field-safely)

## Top level

| Field | Type seen | Notes |
|---|---|---|
| `name` | string | Sometimes `title` instead |
| `description` | string | Long, often over 1,000 characters |
| `url` | string | Canonical product URL |
| `inputUrl` | string | What was submitted, useful when `url` is absent |
| `image` | string or list | Single URL or a list |
| `offers` | object | Never an array in practice |
| `offers.price` | number or string | `248` on Amazon, `"398.99"` on Walmart |
| `offers.priceCurrency` | string | `"$"` on Amazon, `"USD"` on Walmart |
| `brand` | object | Often only has `slogan`; see below |
| `rating` | number or null | Unreliable: `null` on a 4.2-star product, `0` on Walmart |
| `reviewCount` | number | Reliable on Amazon (20,002 observed) |
| `additionalProperties` | object | Where the useful fields actually live |

## Inside additionalProperties

Only present when the call sends `additionalProperties: true`.

The size of this object is per-retailer: **Amazon returned 44 keys, Walmart returned 4.**

Amazon, the keys worth reading:

| Key | Type | Example |
|---|---|---|
| `inStock` | bool | `true` |
| `inStockText` | string | `"In Stock  Only 15 left in stock - order soon."` |
| `stars` | float | `4.2`, the real rating |
| `listPrice` | object | `{"value": 399.99, "currency": "$"}` |
| `asin` | string | `"B09XS7JWHH"` |
| `delivery` | string | `"Saturday, August 22"` |
| `fastestDelivery` | string | Same shape |
| `breadCrumbs` | string | `"Electronics > Headphones..."` |
| `features` | list of strings | Bullet points from the listing |
| `highResolutionImages` | list of strings | Prefer these over `image` |
| `galleryThumbnails` | list of strings | Lower resolution |
| `variantDetails` | list of objects | Each has a `name` |
| `bestsellerRanks` | list of objects | `rank` and `category` |
| `starsBreakdown` | object | Share per star level |
| `aiReviewsSummary` | object | Has a `text` field |
| `monthlyPurchaseVolume` | string | `"3K+ bought in past month"` |

Walmart returned only these four:

| Key | Type | Notes |
|---|---|---|
| `sku` | string | The identifier, since there is no `asin` |
| `currencyRaw` | string | `"$"` |
| `images` | list of objects | `[{"url": ...}]`, not plain strings |
| `descriptionHtml` | string | HTML, not plain text |

Keys that were present on Amazon but held `null`: `condition`, `shippingPrice`,
`priceRange`, `returnPolicy`, `answeredQuestions`, `author`, `support`. Treat any of
these as absent rather than meaningful.

## Per-retailer differences

| Concern | Amazon | Walmart |
|---|---|---|
| Price type | `int` | `str` |
| Currency form | symbol `"$"` | code `"USD"` |
| `brand` content | `{"slogan": "Sony"}` | `{"slogan": "Visit the Sony Store"}` |
| Stock reported | yes, nested | no |
| Rating source | `additionalProperties.stars` | absent |
| Images | string lists | list of objects under extras |
| Identifier | `asin` | `sku` |
| extras size | 44 keys, about 100 KB | 4 keys |

## Reading each field safely

**Name**: `name` or `title`.

**Price**: read `offers.price`. If it is a string, strip everything except digits and
dots. Guard against a stray thousands separator leaving two dots. Reject zero and
negatives. In Python, note that `True` is an `int`, so check for `bool` first.

**Currency**: `offers.priceCurrency`, then `offers.currency`, then
`additionalProperties.currencyRaw`. If the value is alphabetic it is a code and takes
a space before the amount; otherwise it is a symbol and butts against it.

**Brand**: `brand.name`, then `brand.slogan`. Unwrap a `Visit the X Store` pattern to
`X`. Then reject anything longer than about 40 characters or more than four words,
because the slot carries free marketing text and a phrase presented as a brand reads
as fact.

**Stock**: `inStock` at top level, then `additionalProperties.inStock`, then parse
`offers.availability` or `availability` for in-stock and out-of-stock wording. Keep
three states: true, false, and unknown. Never map unknown to false.

**Rating**: `additionalProperties.stars` first, then top-level `rating`. Treat zero as
absent.

**Images**: `additionalProperties.highResolutionImages`, then `image`, then `images`,
then `additionalProperties.images`, then `galleryThumbnails`. Entries may be strings
or objects with a `url`. Deduplicate and drop anything not starting with `http`.

**Identifier**: `gtin`, then `sku`, then `additionalProperties.sku`, then
`additionalProperties.asin`.

**Discount**: compare `offers.price` against `additionalProperties.listPrice.value`.
Report a percentage only when the current price is genuinely lower.

### Gotcha

An unresolvable URL returns an item with every field empty rather than an error, and
`brand` may still be present as `{"slogan": null}`. Treat an item with neither a name
nor a price as "page not recognized", not as a product without a price.

---
name: Bundle Offer Designer
category: ecommerce-retail
summary: Identifies products frequently bought together or with complementary use cases, and designs a bundle offer with pricing that protects margin while giving customers a visible discount.
works_with: [ecommerce-agent, marketing-agent, product-agent]
version: 1.0
---

## WHEN TO USE
Use this skill when looking to raise average order value or move slower-selling items alongside bestsellers. Do not use it to bundle items with no logical relationship purely to hit a price point — mismatched bundles depress conversion and generate returns.

## INPUTS
- Order-history data showing which products are frequently purchased together (order-export or analytics report)
- Individual product margin data for candidate bundle items (finance or profit-margin report)
- Current stock levels for candidate bundle items (inventory system export)
- Store's minimum margin floor for bundle pricing (percentage, default: 20% combined margin if not specified)
- Low-stock threshold per candidate item (unit quantity, from inventory/reorder-point data; default: use each item's existing reorder point as the threshold)

## WORKFLOW
1. Pull order-history data to find products with a high co-purchase rate.
2. Cross-check co-purchased items for a logical, complementary fit rather than coincidence (e.g., camera plus memory card, not camera plus unrelated item).
3. Pull individual margin per candidate item in the proposed bundle.
4. Calculate a bundle price set below the sum of individual item prices, while keeping combined margin at or above the margin floor from INPUTS.
5. If any single item in the bundle is at or below its low-stock threshold from INPUTS, either exclude it from the bundle or cap the bundle's available quantity to match.
6. Name the bundle and write a short value-proposition line describing what the customer gets and saves.
7. Confirm the bundle price is genuinely lower than buying items separately before publishing.
8. Set up the bundle as a listing or checkout-level offer in the store platform.
9. Track bundle sell-through after launch and compare it against the individual items' standalone sell-through.

## OUTPUT SPEC
A bundle offer brief (markdown) with: bundle name, included items and individual prices, bundle price, savings amount/percentage, combined margin check, and stock-cap note if applicable. One brief per proposed bundle.

## EXAMPLE PROMPT
```
We sell home coffee equipment. Our order data shows the pour-over dripper and the gooseneck kettle are frequently bought together. Pull margin on both, design a bundle priced to save customers 15% versus buying separately, and confirm it stays above our 30% margin floor.
```

## QUALITY CHECKS
- The bundle price is mathematically lower than the sum of individual item prices, verified by the actual math shown.
- Combined bundle margin stays at or above the margin floor stated in INPUTS.
- No bundle includes an item currently below its own low-stock threshold without a quantity cap applied.
- The co-purchase relationship cited is backed by actual order data, not assumed complementarity.

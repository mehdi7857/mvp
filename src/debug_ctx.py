from src.exchanges import HyperliquidPublic

def main():
    hl = HyperliquidPublic()
    try:
        meta, ctxs = hl.meta_and_asset_ctxs()

        # ctxs is usually a list aligned with meta['universe']
        universe = meta.get("universe", [])
        idx = None
        for i, u in enumerate(universe):
            if u.get("name") == "ETH":
                idx = i
                break

        if idx is None:
            print("ETH not found in universe")
            return

        eth_ctx = ctxs[idx]
        print("ETH ctx keys:", list(eth_ctx.keys()))
        print("ETH ctx sample:", eth_ctx)

    finally:
        hl.close()

if __name__ == "__main__":
    main()
